# system app 服务层下沉映射清单（Phase B）

> 对应任务台账《CODE-REVIEW-TASKS/04-system-app域拆分与服务层下沉.md》Phase B（T04-04 ~ T04-08）。
> 总原则：**行为零变化**——接口路径、权限码、响应形状、业务码、错误文案、celery 任务名全部不变；
> 仅把业务编排从视图层移入服务层，视图保留请求解析 / 鉴权 / 审计留痕 / 响应构造。
> 上游：Phase A（utils 五域归位，见 [system-utils域拆分映射-2026.10.md](system-utils域拆分映射-2026.10.md)）已完成，
> 服务子模块文件名与其域目录对齐，为 Phase C 四域切分预置迁移单元。

## 一、服务层目标形态

`system/services.py` 单文件契约门面转为 **`system/services/` 包**：

| 模块 | 职责 | 消费面 |
|---|---|---|
| `system/services/__init__.py` | 跨 app 契约门面（原有内容整体保留；`login_success` 由视图再导出改为服务子模块惰性导出） | 其他 app 只允许从这里 import |
| `system/services/token_issue.py` | PAT 凭证签发统一口径（T04-08） | 序列化器 + 开放平台视图 + OAuth 引擎 |
| `system/services/open_oauth.py` | OAuth 2.0 授权码协议引擎（T04-04） | open_oauth 视图 |
| `system/services/auth_login.py` | 登录策略流（成败处置 + MFA 收口编排）（T04-05） | login / mfa 视图；`message` 经门面消费 `login_success` |
| `system/services/file.py` | 文件域：个人统计聚合 + 在线预览状态机（T04-06） | file 管理视图 |

分层约定与 mfa 真服务层对齐：服务层不做请求解析与响应壳；协议型错误以
`(error_code, detail, status)` 三元组 / 领域异常返回，由视图映射为响应。
既有习语保留：`complete_login` 在 MFA 命中时直接返回 `ApiResponse`（登录响应
形状属服务契约，视图原样下发）。

## 二、逐任务映射

### T04-08 令牌签发三处统一 → `system/services/token_issue.py`

| 原实现（三处重复「前缀 + 随机串 → sha256 哈希 + 截断前缀 + create」） | 收口后 |
|---|---|
| `identity/serializers/token.py::PersonalAccessTokenSerializer.create`（`pat_` 个人令牌） | `new_token_secret()` 生成三元组，序列化器保留自身的 create/信号语义 |
| `identity/views/open/open.py::issue_application_token`（`apst_` 应用凭证，含失效旧凭证轮换） | `issue_application_token()`（事务内 `revoke_application_tokens` + `issue_access_token`），视图改调用 |
| `identity/views/open/open_oauth.py::issue_oauth_access_token`（`aoat_` OAuth 访问凭证） | `issue_oauth_access_token()` 委托 `issue_access_token()`，过期时间统一 `application_token_expiry()` |

附带收口：`ApiApplicationViewSet.perform_update` / `regenerate_secret` 中的
「停用即失效全部有效凭证」改调用 `revoke_application_tokens()`（原 inline ORM update）。
前缀常量（`PAT_TOKEN_PREFIX` / `APP_TOKEN_PREFIX` / `OAUTH_ACCESS_PREFIX`）迁入本模块。

### T04-04 open_oauth 协议引擎下沉 → `system/services/open_oauth.py`

| 原位置（views/open/open_oauth.py） | 收口后 |
|---|---|
| 授权码生命周期（`issue_authorize_code` / `consume_authorize_code` / `_code_cache_key`） | 原样迁入 |
| PKCE 校验（`verify_pkce`，仅 S256 fail-closed） | 原样迁入 |
| scope 解析（`resolve_requested_scopes`，锚定口径） | 原样迁入 |
| 双令牌签发（`issue_oauth_refresh_token` + access 委托 token_issue） | 原样迁入 |
| `_validate_authorize_request`（request 参数本就未用） | `validate_authorize_request(data)`，错误改返回协议三元组 |
| `_exchange_code` 事务编排 | `exchange_authorization_code(application, code, redirect_uri, verifier)` |
| `_refresh` 一次性轮换 | `rotate_refresh_token(application, raw_refresh)`；「失效 refresh + 联动失效 access」抽 `_revoke_refresh_row`（轮换/撤销共用） |
| revoke 端点撤销编排 | `revoke_granted_token(application, raw_token)`，返回值即响应 `revoked` 标志 |
| `_token_payload` | `token_payload()` 模块函数 |

视图层保留：`oauth_error` 响应壳、`write_oauth_audit`（module=OAuth 审计留痕）、
凭据校验入口（`verify_application_credentials` 仍在 open.py，本次未迁移）、限流与
匿名可达声明。授权码兑换路径维持**无事务**原语义（access/refresh 分两步落库），
仅 refresh 轮换路径保持事务内（与原实现一致）。

### T04-05 login 策略流下沉 → `system/services/auth_login.py`

| 原位置（views/auth/login.py） | 收口后 |
|---|---|
| `login_failed`（失败计数 + IP 封禁 + 失败日志 + 出站 Webhook + 可读文案） | 原样迁入 |
| `login_success`（密码/账号有效期拦截、锁定计数清理、登录日志、异地/新设备提醒） | 原样迁入 |
| `evaluate_login_policy_for_request`（登录访问策略判定） | 原样迁入 |
| `login_mfa_if_required` + `complete_login`（登录唯一收口） | 原样迁入 |

**契约切断**：`system/services` 门面 `__getattr__` 对 `login_success` 的视图再导出
特例删除，改为 `_LAZY_EXPORTS` 惰性导出服务子模块成员——门面不再 re-export views
成员（`message/notify.py` 等跨 app 消费方 import 面不变）。视图侧 `identity/views/auth/mfa.py`
改从服务层 import `login_success`；视图模块内保留会话登记辅助
（`_register_session_safe` / `_login_type_for` / `SessionTokenObtainPairSerializer`，属请求编排）。

### T04-06 file preview/stats 下沉 → `system/services/file.py`

| 原位置（views/admin/file.py） | 收口后 |
|---|---|
| `stats` 数据装配（配额概览/分类分布/近 7 天趋势/Top 文件） | `build_personal_file_stats(user)`；`_category_stats` / `_recent_trend` 随迁 |
| preview 四态状态机（unsupported / text / image / pdf / office 三态） | `resolve_preview(upload, kind, size)` 返回 `(state, payload)`；业务码 1005/1006 常量随迁 |
| 缓存触点（`touch_preview_cache`）与截断标记 | 状态机内完成（缓存管理属业务语义），视图只做响应映射 |

视图保留：`get_object` 鉴权、`log_file_access` 审计留痕（kind 进明细）、
`cache_response` 短缓存装饰器、`upload` 编排（本次范围外，已薄）。
顺带清理：视图模块内未被引用的 `QUOTA_EXCEEDED_CODE` 重复定义删除
（权威定义在 `system/utils/file/upload_store.py`，行为无影响）。

### T04-07 message views ORM 编排下沉

| 原位置（message/views.py） | 收口后 |
|---|---|
| `open_private` 的目标用户查询 + 开通 | `chat_room_ops.get_or_create_private_room_by_pk`（目标缺失抛可读校验错误） |
| 历史游标分页编排（queryset / 头像批量预取 / can_recall / 倒序） | `chat.history_messages(room, user, before_id, limit)`；`_can_recall` 随迁 |
| 附件上传编排（限额 → 落库临时件 → kind 匹配 → 缓存失效 → 审计） | `attachments.store_message_attachment`，失败抛 `AttachmentUploadError(code, detail)` |
| 附件取件定位（消息存在 → 房间可访问 → 未撤回） | `chat.get_attachment_message`（错误文案保持三分：Message not found / 房间不可访问 / File not found） |
| 撤回广播（房间回查 + 载荷构造 + push） | `message/utils.broadcast_message_recall`（广播归 utils，遵守 chat.py「广播不入领域服务」的既有分层） |

建群 / 成员 / 改名 / 退群在先期迭代已收口 `chat_room_ops`，本次仅登记调用面
（`chat.py` 再导出 `get_or_create_private_room_by_pk` / `store_message_attachment`，
维持「附件链路统一经 chat_service」的调用约定）。

## 三、行为零变化保证点

1. **响应形状/业务码/错误文案逐项保留**：OAuth 错误码映射（`data.error`）、
   `revoked` 布尔口径、`Message not found` 与 `File not found` 的区分、附件上传
   1002/1003/1004/1001 业务码、预览 1005/1006（425）、`X-Preview-Truncated` 头、
   登录失败剩余次数文案等全部原样；`history_messages` / `build_personal_file_stats`
   载荷键集不变。
2. **执行顺序保持**：审计留痕先于预览状态判定、kind 不匹配时不失效缓存不落审计、
   `login_failed` 的日志→Webhook→计数顺序、refresh 轮换的事务边界均与原实现一致。
3. **import 时机保持**：门面保持零顶层业务导入（`login_success` 仍惰性）；出站
   Webhook 的函数级 import 位置不变（import 链语义不变）。
4. **跨 app 门禁**：`check_cross_app_imports` 通过（契约缝 5 条不变，`packages/xadmin-common/common/contracts.py`
   提供方声明无需变更）；`check_file_length` 通过（新服务模块最大 ~230 行）。

## 四、测试与回归

- 测试同步更新（patch-where-used 原则，逻辑移到哪层就 patch 哪层）：
  `test_oauth.py`（complete_login / MFA 判定 patch 目标 → `system.services.auth_login`）、
  `test_login_alert.py` / `test_user_invite.py` / `test_webhook_api.py`（`login_success` import 面）、
  `test_oauth_authorize.py`（授权码缓存键造数改走服务模块）、
  `test_chat_api.py`（撤回广播 patch 目标 → `message.utils.push_room_event`）。
- 全量 pytest 通过（真环境 PG + Redis 档）；存量环境抖动项
  `test_reentrant_lock`（Redis 计时敏感）在基线（未含本批改动）同样偶发，与本批无关。
- 门禁：ruff check / format、跨 app import 门禁、文件行数门禁、文档路径守卫
  （`docs/框架开发遵循准则.md` 的 `system/services.py` 引用同步改为包路径）、
  文档事实校验、缓存键前缀门禁全部通过。

## 五、对 Phase C 的接口

- 四域服务子模块即迁移单元：`token_issue` / `open_oauth` / `auth_login`（identity）、
  `file`（file 域）；域切分时整目录随域迁出，跨 app 消费面已收敛到
  `system.services` 门面（`login_success` 一条）。
- `message` 侧遗留观察项：`message/views.py` 的 `attachment_response` 直连
  （取件响应构造，属视图层合理职责）；WS consumer 的撤回广播为 async 通道
  （`self.broadcast`），与 REST 侧 `broadcast_message_recall` 载荷形状已一致。
