# ADR-052：JumpServer 对标批三「好用功能补齐」（F 线八项）

| 项目 | 内容 |
|------|------|
| 状态 | 已实施（2026-09-23） |
| 关联 | [JumpServer 对标完善方案](../plans/JumpServer对标完善方案-2026.09.md) §四 批三 · [ADR-050](ADR-050-jumpserver-batch1-security-and-decision.md)（批一）· [ADR-051](ADR-051-jumpserver-batch2-ai-platform-and-ux.md)（批二） |
| 影响面 | 后端：system / notifications 两 app（+2 迁移）、+26 权限点、+2 页面菜单；前端：5 页面接线 + 3 新页面区块 + 2 新增页面 |

## 1. 决策摘要

按方案 §四 批三清单交付八项「好用功能补齐」，全部遵循三条既有约束：

1. **机制级借鉴、不搬体系**（ADR-015 §2）：能力落进本仓元数据驱动 / 三层权限 / `ApiResponse` 契约，不引入新框架；
2. **零行为变化默认**：新增能力默认不改变既有链路行为（模板覆盖未配置 = 代码默认；批量更新字段白名单显式声明；上传策略黑名单 + 可选白名单）；
3. **fail-closed 与可观测**：危险动作（策略拒绝、上传扩展名、聚合字段越权）一律拒绝并留痕（登录日志 `policy_result` / `FileAccessLog` / `OperationLog`）。

## 2. 交付清单

| 编号 | 能力 | 关键实现 |
|------|------|----------|
| F-1 | 通用批量更新 | `_batch_update_payload` helper + `batchUpdateAction`（白名单：用户 / 角色 / 部门 / 字典 / 定时任务）；前端 `useBatchUpdate` + `BatchUpdateForm`（字段白名单 + 布尔/数字/选择/文本四类输入），`_write_marker=batchUpdate` 便于审计识别 |
| F-3 | 通知消息模板可配置 | `MessageTemplate` 覆盖表 + `notifications/template_registry.py`（60s 缓存、沙箱渲染、变量白名单校验）；渲染收口在 `Message.get_backend_msg_mapper` —— 邮件 / 站内信 / 短信 / IM 全渠道一致；`message-templates` 注册表 / preview / save / reset 四端点挂 `SettingMessage` 菜单 |
| F-4 | 列表「我的视图」 | `SavedListView`（owner + page_key + 条件快照 + 默认视图互斥 + 可共享）；`saved-views` CRUD（个人取值域，剥离数据权限过滤，同 PAT 口径）；前端下拉见「边界」 |
| F-5 | 审批协作增强 | `ApprovalInstanceComment`（讨论区）+ `comment` / `comments` / `comment/delete` 三端点（@用户名 提醒）；抄送人 = 可达节点 `cc_users` 并集 + 发起时追加（去重、不含申请人、落实例快照），抄送人进入实例可见域并收终态知会 |
| F-6 | 账号安全风险巡检 | `AccountRisk` + `account_risk.py` 六类巡检（密码过期 / 长期未改密 / 长期未登录 / 从未登录 / 管理员未绑 MFA / 超管数量异常）；幂等（同用户同类型一行）、风险消失自动 `RESOLVED`、人工 `IGNORED` 不被翻回；处置动作六种（通知 / 强制改密 / 强制下线 / 停用 / 豁免 / 标记已处理）+ 留痕；每日 04:23 周期巡检任务；`must_change_password` 经登录响应引导改密（改密即清除） |
| F-7 | 登录访问策略 | `LoginAccessPolicy`（优先级 + 星期 + 时段（支持跨天）+ 网段（CIDR / 区间）× 全部用户 / 角色 / 用户；动作 accept / reject / require_mfa / record）；账密与验证码登录双链路判定，命中写 `UserLoginLog.policy_result`；`preview` 命中预演（逐条匹配 + 最终生效）；并发会话上限 `SECURITY_LOGIN_MAX_SESSIONS`（超限踢最久未活跃会话 + 会话级令牌失效） |
| F-8 | 文件访问审计 + 上传安全 | `FileAccessLog`（上传 / 下载 / 预览 / 删除留痕 + 失败留痕）+ 受鉴权 `download` 端点（替代 `/media/` 直链）+ `access-logs` 查询；上传扩展名策略（黑名单默认拒绝可执行类 + 可选白名单，fail-closed）并经 `config` 下发；访问日志保留期 `FILE_ACCESS_LOG_KEEP_DAYS` + 每日清理任务；审计关系不计入「附件是否在用」判定（`NON_BUSINESS_RELATIONS`） |
| F-9 | WebAuthn / Passkey 与认证方式策略 | `UserPasskey` + `system/utils/webauthn.py`（自实现 CBOR 解析 / COSE 公钥 / ES256 验签，零新依赖）；`/api/system/passkeys`（challenge / register / 列表 / 删除，个人凭据白名单路由）+ 登录前匿名挑战端点；`PasskeyBackend` 作为 MFA 方式（`challenge_required=False`，同一 `check_code` 链路服务登录与 412）；认证方式策略三层收敛（全局 `SECURITY_MFA_METHODS` ∩ 角色 `allowed_mfa_types` ∩ 用户 `allowed_mfa_types`）+ 角色 `mfa_required` 强制（无可用方式时降级放行防死锁） |

### 2.1 权限点与种子

- 新增 26 个权限点（账号风险 5 / 登录策略 6 / 审批评论 3 / 批量更新 5 / 消息模板 4 / 文件下载与访问记录 2 / 审批其他）+ 2 个页面菜单（`SystemAccountRisk` 挂 `logs`、`SystemLoginPolicy` 挂 `settings`）已由 `sync_menu_permissions --update-seed` 写入 `loadjson/menu.json` + `menumeta.json`；
- Passkey 与「我的视图」为**个人资源**，走 `PERMISSION_WHITE_URL`（同 PAT / MFA 口径），视图内收口本人；
- 新增 `PARENT_MENU_MAP` 三条映射（account-risks → SystemAccountRisk、login-policies → SystemLoginPolicy、message-templates → SettingMessage），扫描缺口归零（`test_permission_seed_coverage` 守护）。

## 3. 关键取舍

| 取舍 | 理由 |
|------|------|
| Passkey 自实现 CBOR / ES256 验签（而非引入 `webauthn` 库） | 红线要求零重依赖；仅需注册（`fmt=none`）与断言验签两类最小路径，实现约 200 行且全部有单测覆盖（注册-认证往返 / 挑战一次性 / 篡改签名 / 计数器回退） |
| 模板渲染用 Django 模板沙箱 + 变量白名单 | 复用框架能力，不新写模板引擎；白名单校验与语法校验在保存时拦截（不开放任意模板逻辑） |
| 批量更新字段白名单由页面声明 | 避免把关联 / 上传类字段暴露给批量写入；白名单显式化也让「批量改了什么」在代码层可审计 |
| 登录策略在「密码校验通过后」判定 | 避免匿名探测策略信息；拒绝时绑定 `request.user` 记失败日志（可追溯被拒账号） |
| 文件下载走 DRF 端点而非 `/media/` 直链 | 鉴权 + 数据权限 + 访问审计三者缺一不可；保留 `/media/` 兼容既有外部引用 |
| `apply_template_override` 存在性保护 + DB 异常降级为空覆盖 | 通知渲染绝不能被模板层故障拖垮（unit 测试无 db 访问场景亦安全） |

## 4. 验证

- 后端：`pytest` 全量 **exit 0**（新增 7 个测试文件 / 约 90 例：账号巡检 12、登录策略 14、文件审计 8、Passkey + 认证策略 13、消息模板 15、我的视图 8、审批协作 10）；`ruff check` / `ruff format --check` / 行数门禁 / 跨 app / 缓存键 / `makemigrations --check` 全绿；
- 前端：`vue-tsc`（默认 + strict 全仓）/ `eslint`（`--max-warnings 0`）/ `prettier` / `stylelint` / 行数门禁 / `vitest` **317 passed** 全绿；
- 迁移：`system/0009`（抄送 / 认证策略 / 强制改密 / 策略结果 + 五个新模型）、`system/0010`（SavedListView）、`notifications/0004`（MessageTemplate）。

## 5. 边界与遗留

| 项 | 状态 / 说明 |
|----|------|
| F-4 前端「我的视图」下拉 | ✅ **已交付（2026-09-23）**：RePlusPage 新增 `savedViews` 开关 + `SavedViews.vue`（命名保存 / 一键套用 / 默认视图 / 共享只读 / 删除 / 设默认），用户管理（桌面 + 移动双形态）、角色、文件中心、定时任务四页已开启；默认视图仅在搜索区为空时静默套用（不覆盖手输条件） |
| F-5 抄送人前端选择 | ✅ **已交付（2026-09-23）**：流程设计器节点表新增「抄送人」列（用户名多选，与审批人同口径的远程搜索 + 直接输入兜底）+ 发起弹窗新增「抄送人」多选；后端 `_resolve_instance_cc` 标识兼容**用户名与 pk** 两种形态（节点存用户名、API 传 pk 均可） |
| F-6 引导改密落地页 | ✅ **已交付（2026-09-23）**：`must_change_password` 随 `userinfo` 下发（`data.must_change_password`）→ user store 新增 `mustChangePassword` → `App.vue` 观察后弹引导框跳「个人配置」（每会话一次，改密即由后端清除标记），刷新页面仍生效 |
| F-9 原生认证器探测 | 真实认证器的 E2E 冒烟依赖 HTTPS 与系统级凭据，当前仅单测（本地密钥自造）覆盖；浏览器侧 `navigator.credentials` 分支待人工验证 |
| 日志保留期默认值 | `FILE_ACCESS_LOG_KEEP_DAYS` 默认 0（不清理），需按合规要求显式配置 |

## 6. 后续

1. 批四（E-1 pyproject + uv、P-5 审计归档、U-5 大屏导图、F-10 OIDC 等）按触发条件启动；
2. 「我的视图」逐步在其余列表页开启（已具备零成本开关）；如需「视图随列偏好跨设备同步」再与 U-3 表头排序一并评估。
