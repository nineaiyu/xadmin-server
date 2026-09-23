# ADR-054：JumpServer 对标剩余项收口（U-3 表头排序 / P-4 存储后端 / F-10 OIDC / 遗留清账）

| 项目 | 内容 |
|------|------|
| 状态 | 已实施（2026-09-23） |
| 关联 | [JumpServer 对标完善方案](../plans/JumpServer对标完善方案-2026.09.md) §四 批二遗留（U-3 表头排序）与批四触发制项（P-4 / F-10）· [ADR-050](ADR-050-jumpserver-batch1-security-and-decision.md)（批一）· [ADR-051](ADR-051-jumpserver-batch2-ai-platform-and-ux.md)（批二，U-3 遗留登记）· [ADR-052](ADR-052-jumpserver-batch3-usability-and-security.md)（批三）· [ADR-053](ADR-053-jumpserver-batch4-triggered-and-engineering.md)（批四，P-4/F-10 触发制登记与 P-5/F-12/F-13 遗留） |
| 影响面 | 后端：search-columns 元数据 `sortable`（契约扩展）、`common/storage/`（新包）+ `STORAGES` 装配 + 文件链路适配（下载 / 预览 / 媒体兜底 / 缩略图）、`manage.py storage_migrate`、health `storage_status`、`system/utils/oidc.py`（新）+ OAuth flavor/nonce/组角色同步、`ai_tool_audit` triage 清单（新）、登录日志自动归档、字典与审批流程计数迁移声明式；前端：RePlusPage 表头排序（`useTableSort`）+ 高级筛选（`AdvancedFilter.vue` + `advancedFilter.ts`）与两页开启、oauth 类型注释；文档：`ops/storage.md`（新）、`oauth-login.md` §六、`log-archive.md`（自动面 + 演练记录）、文档站 `storages.md` 升级 |

## 1. 决策摘要

把方案中**全部未完成项**收口：批二遗留的 U-3 表头排序、批四「触发未命中」的 P-4 文件存储后端与
F-10 标准 OIDC，以及四个 ADR 登记的边界与遗留（ai_tool_audit 缺口收敛入 CI、登录日志自动归档、
F-12 存量迁移、F-13 前端高级筛选、冷归档恢复演练、approval-chain E2E 失败根因）：

1. **声明式优先、契约先行**：U-3 的 `sortable` 是元数据契约扩展 —— 服务端 schema（`docs/schema/`）、
   前端镜像 + 生成类型 + 手写类型四处同源；F-10 的组角色映射复用 LDAP 口径；
   F-13 前端只在后端 opt-in 的页面开启（props 开关，未开启页面零变化）；
2. **可用性优先的回退链**：P-4 的可选依赖缺失 / 配置不全 → 回退本地并输出去重告警（文件链路不中断）；
   OIDC discovery / JWKS 不可达 → 可读业务文案（不回显 IdP 报文）；
3. **高危动作最小面**：存储后端切换走系统配置（运行期热生效）、凭据经 signer 加密落库；
   OIDC 只增删映射内角色、绑定链路不触发角色同步；
4. **遗留必须闭环**：巡检「0 未登记缺口」入 CI（新资源域出现即红）；冷归档恢复演练落成
   integration 用例并写入演练台账；approval-chain 失败项定位到「审批列表不实时推送」并修正用例时序。

## 2. 交付清单

| 编号 | 能力 | 关键实现 |
|------|------|----------|
| U-3 | 表头排序（批二遗留） | search-columns 逐字段下发 `sortable`（**仅 ViewSet `ordering_fields` 声明面**；`__all__` 视为全部；未声明不下发）；契约四处同源（服务端 schema / 前端镜像 / `pnpm gen:metadata-types` 产物 / 手写 `SearchColumnsResult`）；前端 `PageColumn.sortable = "custom"` + 新 `useTableSort`（与搜索区 ordering **同源**：点击 caret 写 `ordering=field`/`-field` 并回第一页；外部改 ordering 回显表头标记；`syncing` 防自循环；**初始化赋值不回显** → 页面加载零视觉变化）；用户页 / 操作日志页等 40+ 视图集自动获得（凡声明 order 检索项的表格页） |
| P-4 | 文件存储后端可插拔（触发制落地） | `common/storage/SwitchableStorage`（委托 local FileSystemStorage / S3，**配置指纹缓存 + 运行期热切换**；`django-storages` 为可选依赖，未装 / 桶未配 → 回退本地 + 去重告警）；9 个 SysConfig 键（`FILE_STORAGE_BACKEND` / `FILE_S3_*`，密钥经 signer 加密并登记 `SENSITIVE_SETTING_KEYS`）；适配层 `common/storage/utils.py`（`storage_exists/open/size/local_path/url/probe`，**业务禁 `filepath.path`**）；文件链路适配：受鉴权下载（file_access / record_base）、PDF 内联预览、`/media/` 兜底（远端回落 storage 读取）、图片/文本/Office 预览（远端先落 `MEDIA_ROOT/storage_cache`，与预览缓存同任务清理）、`ProcessedImageField` 缩略图判断改运行期；`manage.py storage_migrate`（push/pull、幂等断点续搬、默认不覆盖冲突、`--verify [--md5]`）；health 增 `storage_status/storage_time`（**不参与 status 判定**）；文档 `docs/ops/storage.md` + 文档站 `advanced/storages.md` 升级（手工方案降为附录） |
| F-10 | 标准 OIDC 单点登录（触发制落地） | 新 flavor `oidc`（`FLAVOR_PRESETS` / `FLAVOR_REQUIRED_KEYS`）；`system/utils/oidc.py`：**discovery**（`issuer` 推导或 `discovery_url`，缓存 10min）+ **id_token JWKS 验签**（kid 选取、算法白名单禁 none/HS*、`iss`/`aud`/`exp`/`nonce` 校验、密钥轮换失败强制刷新一次 JWKS）+ claims → 资料字段映射（`nickname_claim`/`email_claim`/`phone_claim`）+ **组 → 角色映射**（`groups_field` + `group_role_map`，只增删映射内角色，口径同 LDAP）；`nonce` 与一次性 state 绑定（`issue_nonce`/`consume_nonce`，授权下发、回调校验）；回调登录链路在 `complete_login` 前同步角色（绑定链路不同步）；复用既有 OAuth 路由与白名单（**零新增权限点**） |
| 收口 1 | ai_tool_audit 缺口收敛 + 入 CI | 新 `system/utils/ai_tool_triage.py`：**资源域三态清单**（已声明 / exempt 带理由 / 未登记或 register → 缺口）；384 个未注册候选收敛为 33 个资源域的显式决策（全部 exempt 带理由）；守护用例 `test_no_unconfirmed_gap`（`candidates == []`）+ 注入式负向断言（新资源域 → `--fail-on-gap` 退出码 1）→ 新模块出生即被 AI 面感知 |
| 收口 2 | 登录日志自动归档 / 清理 | `LOGIN_LOG_RETENTION_DAYS`（默认 365，进种子与单源守护）；`log_archive.retention_days` 支持 login；`auto_clean_operation_log` 扩展为 operation + login 双对象「先归档后清理」；`log_archive --model login --prune` 放开（保留期 0 时给可读提示）；新增 4 例（自动清理 / 保留期关闭 / 恢复演练 / 命令面） |
| 收口 3 | F-12 存量迁移声明式 | 字典 `children_count`、审批流程 `node_count` 改 `relation_count_fields` 声明（注解名 = 字段名），视图删手写 `get_queryset().annotate()`；语义微调：字典子节点显示其真实子项数（原为「非根恒 0」） |
| 收口 4 | F-13 前端高级筛选 | `RePlusPage` 新增 `advancedFilter` 开关 + `AdvancedFilter.vue`（字段来自列元数据 / lookup 八项与后端逐字对齐 / `in` 逗号多值 / `isnull` 布尔下拉 / 逐行错误提示）+ `advancedFilter.ts` 纯函数（`buildLookupParams` / `parseLookupConditions` / `stripLookupConditions`）；应用时先清旧 lookup 键再写新条件并回第一页；条件随 searchFields 进「我的视图」快照；用户页（双形态）与操作日志页开启 |
| 收口 5 | 冷归档恢复演练 | 演练用例（operation 全链路：归档 → `--verify` → `--restore-range --grep` → **篡改检测退出码 1**；login 链路：归档 → 校验 → 恢复 → 水位清理 → 幂等）；`docs/ops/log-archive.md` 演练表落 2 行（含结论），去掉「待首次执行」 |
| 收口 6 | approval-chain E2E 修复 | 根因 = **审批列表不做实时推送**：第二个单提交后 pageB 停留在旧列表（首个单能过是因为 pageB 刚导航），WS 不触发列表刷新；修复 = 等待单 B 行前显式 `reload()` + `openMenuPath` 并配合 `waitNotificationsGone`；双浏览器 2 passed，删除 README「待深挖」条目并写回处置 |

## 3. 关键设计决策

### 3.1 U-3：`sortable` 契约扩展与「初始不回显」

- **契约扩展方式**：`additionalProperties: false` 的 schema 新增允许键（非语义破坏），
  但必须四处同源（服务端 schema 为真源 → `pnpm sync:contract` 同步镜像并重生成类型），
  否则既有契约守护（`test_metadata_schema.py` / `check:contract`）会红；
- **下发面 = 声明面**：仅 `ordering_fields`（含 `-` 前缀规范化，`__all__` 全量）中的字段下发
  `sortable: true` —— 未声明排序的视图集零变化，避免「能点但排不了」；
- **状态同源**：`searchFields.ordering` 是唯一排序状态载体（表头点击写它、搜索区 ordering 下拉写它、
  回显从它解析），不引入第二份状态；`syncing` 标志忽略程序性 `table.sort()` 派发的 sort-change；
- **初始化赋值跳过回显**：元数据装配写入默认 ordering 时不调用 `table.sort()` → 表头初始无排序标记、
  视觉基线与既有页面零变化；此后任何 ordering 变化（含重置回默认）同步标记。

### 3.2 P-4：为什么用「委托存储」而不是改 settings

- settings 不在 import 期读 SysConfig（既有约定），而存储必须**运行期可切换**：
  故 `STORAGES["default"]` 固定指向 `SwitchableStorage`，由它按配置指纹解析并缓存委托实例
  （local ↔ s3 切换在下一次文件操作即生效，无需重启）；
- **回退链**：s3 缺 `django-storages` / 缺 bucket / 构建参数非法 → 回退本地 + 去重 WARNING
  （宁可降级可用，不静默失败也不阻断上传）；配置读取异常同样回退本地；
- **业务只走适配层**：对象存储没有本地路径，`filepath.path` 在全链路被替换为
  `storage_open` / `storage_local_path`（后者把远端对象落到 `storage_cache` 供 PIL / LibreOffice 使用，
  与预览缓存同任务按最近使用清理）；`/media/` 兜底在本地缺失时回落 storage 代理读取；
- **搬迁命令**：幂等（目标存在且大小一致 → 跳过 = 断点续搬）、默认不覆盖（大小不一致记 `conflict`，
  `--overwrite` 显式覆盖）、`--verify [--md5]` 校验；退出码非 0 便于接巡检。

### 3.3 F-10：验签与身份映射的安全边界

- **算法白名单 + kid 选取**：拒 `none` / HS*（防算法混淆），JWKS 无 kid 时仅唯一密钥可用；
  首次验签失败强制刷新 JWKS 一次（密钥轮换兜底）；
- **nonce 与 state 解耦存储**：nonce 与一次性 state 绑定（Redis TTL 同 state），
  授权时下发、回调校验后即失效；不改变既有 state 语义（零回归）；
- **claims 映射白名单**：只映射显式声明的资料字段（默认 name / email / phone_number），
  建号用户名仍走 `provider_subject` 规则（不做 IdP 用户名对齐，防命名劫持）；
- **组 → 角色只收敛授权面**：仅增删 `group_role_map` 内的角色（管理员手工授予的映射外角色不受影响），
  只在登录链路同步（绑定链路不触发，避免借绑定改权限）；
- 依赖说明：`id_token` 验签使用 PyJWT + cryptography（由 `djangorestframework-simplejwt` 的传递依赖带入，
  未在 `pyproject.toml` 显式声明 —— 若后续更换 JWT 实现需同步登记）。

### 3.4 遗留收口：巡检 triage 与「0 缺口」的边界

- 全部 384 个候选按**资源域**登记决策（33 条），而非逐端点注册动作 —— 巡检的守护价值定位为
  「**新资源域出现即红**，强制做一次『注册动作 or 登记不 AI 化』的决策」；
- `register` 决策计入缺口（推动补声明），`exempt` 必须带理由（审计可查，`--show-exempted` 输出）。

## 4. 边界与遗留

- **P-4**：`django-storages` / `boto3` 为可选依赖（不入 requirements，需按文档手工安装，
  容器重建后需重装）；预签名直传未做（上传仍经服务端中转）；搬迁期间的「双写 / 只读窗口」设计与演练未做（单机切换窗口短，登记为按需）；
- **F-10**：CAS / SAML 维持评估出口（触发条件：真实 IdP 仅支持该协议）；OIDC 端点强制 HTTPS
  （内网 http IdP 需 https 反代）；`id_token` 验签依赖未显式声明（见 §3.3）；
- **U-3**：仅 `ordering_fields` 声明的列可排序；复合排序（多字段）回显只取首个字段；
  「我的视图」仍不保存 ordering（视图语义 = 筛选条件）；
- **F-13**：前端高级筛选仅在 opt-in 的后端视图（用户 / 操作日志）开启；其条件不参与「我的视图」的
  ordering 剔除逻辑（lookup 键会被 `stripLookupConditions` 清理后再写入）；
- **收口 2**：登录日志保留期默认 365 天，正式环境升级需 `load_init_json` 重灌种子后生效；
- **收口 3**：字典 `children_count` 语义微调（子节点显示真实子项数，原为常量 0）。

## 5. 验证

- **后端**：pytest 全量 exit 0 + ruff / 行数 / 跨 app / 缓存键 / `makemigrations --check` / 文档门禁全绿；
  新增测试：U-3 表头排序 11 例、P-4 存储后端 12 例 + 搬迁 7 例、OIDC 22 例、巡检 triage 2 例、
  归档 login 4 例、health 探测 1 例（共 +59 例）；
- **前端**：`typecheck` / `typecheck:strict` / `eslint` / `prettier` / `stylelint` / `vitest 340`（+16）/ 行数门禁；
  契约 `sync:contract` + `check:contract`（search-columns schema 扩展）；
- **E2E**：新增 `e2e/table-sort.e2e.ts`（U-3 + F-13）双浏览器 4 passed；
  `approval-chain`「初审驳回终止」修复后双浏览器 2 passed；教训已登记 `e2e/README.md`。
