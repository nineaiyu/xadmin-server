# ADR-017：LDAP/AD 目录同步（bind 认证 + 定时同步）

- 状态：已接受
- 日期：2026-09-12
- 关联：年度开发计划 2026.10-2027.09 §四 W1（G1）；ADR-008（PAT 凭证边界）；
  SCIM 目录（云 IdP push 模式，本 ADR 为其本地 IdP 对应项，pull 模式）；
  计划风险表「LDAP/IM 涉及外部凭据 → 凭据全部走值级加密」

## 背景

xadmin 的目录能力只有 SCIM（云 IdP 向系统 push 用户/组），缺少本地目录协议
LDAP/AD 的 pull 接入。政企环境的本地 AD/OpenLDAP 是事实标配：员工账号在目录
服务器统一维护，期望「目录账号可直接登录平台 + 组织架构定时同步」，目录断连
时本地账号必须照常可登录（失败降级）。

本 ADR 定 G1 窗口方案：LDAP bind 登录（与本地账密并存、优先级可配）、用户/
部门/状态定时同步（字段映射 + 软删与回收站策略 + 冲突审计）、管理页与连接
测试、越权/集成测试与 E2E 主链路。

## 决策

### 1. 依赖与代码边界：ldap3 + `system/ldap/` 包，配置走 Setting 体系

- 协议库选 **ldap3**（纯 Python、无系统级依赖、支持显式 LDAPS/StartTLS）；
  不引 django-auth-ldap：其配置模型是静态 `AUTH_LDAP_*` 常量，与「运行期可改、
  优先级可配、部门树同步、冲突审计」的自研需求不匹配，引它只省 bind 的几十行
  却多一整层配置翻译。
- 代码位置：`system/ldap/`（`client.py` 连接封装 / `auth.py` 认证 backend /
  `sync.py` 同步服务 / `tasks.py` 周期任务），与 `system/scim/` 对称；新增模型
  仅 `LdapUserBinding`（用户 FK + dn 唯一 + 最近同步时间）。
- 配置面复用 `settings` app 的 **Setting 体系**（category=`ldap`，与邮件服务
  器配置同构）：序列化器字段即配置名、`write_only` 字段自动**值级加密**
  （BIND_PASSWORD 落库即密文、API 永不回传），保存后经既有
  `refresh_settings_on_changed`（Redis pub/sub）热更新 `django.conf.settings`，
  启动时 `refresh_all_settings` 回灌。认证 backend 与同步任务读
  `settings.LDAP_*`，默认值登记在 `server/conf.py`。

### 2. 登录接入：认证 backend 链 + `complete_login` 唯一收口（不新增登录端点）

- `AUTHENTICATION_BACKENDS = ["system.ldap.auth.LdapBindBackend", ModelBackend]`。
  账密登录仍是 `BasicLoginAPIView`：临时 token / 图形验证码 / AES 报文加密 /
  登录限流 / 账号·IP 防爆破等前置防护与 `complete_login` → MFA / UserSession /
  登录日志 / 失败计数清理等后置收口**全部自动继承**，不存在第二条可绕过收口的
  登录路径。
- 优先级在 backend 内动态实现（backend 链顺序固定，行为随配置）：
  - `LDAP_AUTH_ENABLED=False`（默认）→ 立即返回 None，行为与现状完全一致；
  - `LDAP_AUTH_PRIORITY="local_first"`（默认）→ 本地已存在且持有可用密码的
    用户直接让位 ModelBackend（防止目录密码遮蔽本地管理员密码）；仅对「本地
    不存在或无可用密码」的用户尝试 bind；
  - `LDAP_AUTH_PRIORITY="ldap_first"` → 先 bind 目录，成功即返回用户（本地
    密码仍在链上作为后备）。
- **失败降级**：bind 失败 / 目录不可达一律 `return None` 落回 ModelBackend +
  warning 日志，绝不抛异常阻断本地登录；未启用时不产生任何连接开销。
- 用户解析与建号：按 `LdapUserBinding.dn` 精确匹配 → 退回 username 复用本地
  账号（自动补建 binding）；`LDAP_AUTH_AUTO_CREATE`（默认开）时为无同名账号的
  目录用户 `create_user + set_unusable_password`（无本地密码，杜绝本地爆破面，
  与 OAuth auto-create 同口径）。
- 登录日志：`LoginTypeChoices` 新增 `LDAP = 3`；backend 成功后以
  `user._ldap_authenticated` 标记，`SessionTokenObtainPairSerializer` 透传为
  login_type。
- 改密入口拦截：存在 `LdapUserBinding` 的用户在本地改密/管理员重置入口被拒
  （密码由目录服务器管理）；密码过期检查对 LDAP 用户天然豁免
  （`date_password_updated` 恒为 NULL 走宽限放行）。

### 3. 定时同步：OU→部门树、条目→用户，失败域隔离 + 冲突审计

- `@register_as_period_task(crontab="17 * * * *")` 每小时执行；任务入口先查
  `LDAP_SYNC_ENABLED`（默认关），管理页可随时停用；手动触发复用周期任务管理页
  的 run 动作（自动获得 TaskExecution 执行历史）。
- **部门**：`LDAP_DEPT_SEARCH_BASE` 下按 DN 层级把 OU 同步为 `DeptInfo` 树
  （`code="ldap:"+规范化DN` 命名空间隔离、`name`=OU 名、父级按 DN 前缀推导），
  写后调用 `invalid_dept_tree_cache()`；`LDAP_DEPT_ENABLED=False` 时用户不绑
  部门（保持本地归属不被同步覆盖）。
- **用户**：`LDAP_USER_SEARCH_BASE` + `LDAP_USER_FILTER`（默认
  `(objectClass=person)`）分页搜索；字段映射固定四键
  `LDAP_ATTR_MAP`（username/nickname/email/phone，默认
  sAMAccountName/cn/mail/telephoneNumber，OpenLDAP 可在管理页改 uid 等）。
- 写入策略：已有 binding → 更新映射字段与 `is_active`（AD `userAccountControl`
  禁用位，OpenLDAP 恒启用）；无 binding 且 `LDAP_SYNC_AUTO_CREATE`（默认开）→
  建号并落 binding。单条目 savepoint 隔离，逐条失败不中断整批。
- **冲突审计**（默认 skip + 登记）：目标 username 已被本地无绑定用户占用、
  email/phone 唯一冲突 → 跳过并逐条 `OperationLog(module="LDAP:conflict",
  auth_type=LDAP)` 审计，摘要写入任务日志。
- **目录侧消失策略** `LDAP_SYNC_MISSING_POLICY`（枚举，默认 `deactivate`）：
  - `deactivate`：置 `is_active=False`（可逆、无损，推荐默认）；
  - `soft_delete`：进回收站（复用 SoftDeleteModel 语义，用户名仍被占用，目录
    恢复后可 restore）；
  - `ignore`：不动本地账号。
- **摘要通知**：同步结束 conflict / deactivate / soft_delete 非零时发
  `SystemMessage` 通知超管（每任务一条，不逐条轰炸）。
- 连接失败 → 任务整体 FAILURE（TaskExecution 留痕 + 日志），不影响在线登录。

### 4. 管理页与连接测试

- 后端 `LdapServerSettingViewSet`（`settings/views/ldap.py`，
  NoDetailRouter → `/api/settings/ldap`）：`GET retrieve` 回显配置
  （BIND_PASSWORD 永不回传）；`PUT partialUpdate` 保存（write_only 加密）；
  `POST create` = **连接测试**（服务账号 bind → 按当前 filter 实际搜索并返回
  用户/部门条目计数；异常转 `ApiResponse` 1001/1002 可读错误）——与邮件设置
  「保存即测试」范式同构。
- 菜单种子：`loadjson/menu.json` + `menumeta.json` 增量（settings 目录下
  `SettingLdap` 菜单 + retrieve/partialUpdate/create 三个权限点）。
- 前端 `xadmin-client/src/views/settings/ldap/index.vue`：复用 `SettingItem`
  （自带保存/重置/测试按钮与权限位）。

## 后果

- **明示不做**（登记边界，转候选池需重新评审）：LDAP 组→角色映射（一期只做
  部门树）、多 LDAP 服务器（单目录实例）、嵌套组展开、目录侧密码策略同步
  （密码策略以本地为准）、自签证书 TLS 定制（ldap3 默认校验，需求出现时再开
  配置面）。
- **降级矩阵**：LDAP 关闭 / 不可达 → 本地登录零影响；同步失败 → 任务 FAILURE
  + 通知，不产生半同步状态（逐条 savepoint）。
- **安全**：BIND_PASSWORD 值级加密 + write_only 不回传；LDAP 路径与本地路径
  共享同一条登录防护链（限流 / 防爆破 / 锁定 / MFA）；越权面 = 设置接口按
  菜单权限点控制（retrieve/partialUpdate/create 三点独立授权）。
- 配置热更新依赖既有 Setting pub/sub；多进程（gunicorn worker / celery）下
  一致生效。

## 测试与验收

- 单元：`client.py`（fake 连接注入）、`auth.py`（开关 / 优先级 / auto-create /
  复用本地账号 / 异常降级不抛出）、`sync.py`（建号 / 更新 / 冲突跳过与审计 /
  禁用 / 软删 / ignore / 部门树与缓存失效）。
- 集成：settings API（加密回显不泄密 / 越权 403 / 连接测试失败路径）、登录
  成功记 `login_type=LDAP`、LDAP 用户改密被拒、周期任务注册与手动触发。
- E2E 主链路：管理员登录 → 系统设置-LDAP → 表单渲染 → 保存配置 → 连接测试
  （无真实目录时得可读失败提示）→ 本地账密登录不受影响。
- 门禁：后端 `pytest -n auto --cov --cov-fail-under=78`、ruff format/check、
  跨 app import 门禁、i18n po 门禁；前端 lint / typecheck / locale-keys 双语
  对齐门禁；E2E smoke 全绿。
