# ADR-030：开放平台雏形（G11，client-credentials 应用）

- 状态：已接受（2026-09-13 实现落地）
- 日期：2026-09-13
- 关联：年度开发计划 2026.10-2027.09 §四 2027-08（G11）；ADR-008/ADR-022（PAT 与 webhook 签名）；
  `system/models/token.py::PersonalAccessToken`、`common/core/auth.py`（`hash_pat_token` /
  `PersonalAccessTokenAuthentication`）、`common/core/permission.py`（`resolve_pat_scopes` /
  `PatScopePermission`）、`system/utils/webhook.py`（HMAC 签名）

## 背景

机器对机器（M2M）接入目前只有**个人访问令牌（PAT）**：凭证绑定到自然人（creator），审计与人共用身份，
没有「应用」这一实体，也没有按应用维度限流与回调注册。G11 要一个 client-credentials 应用的雏形：
**应用自主换发凭证、按应用限流、回调注册与测试**，并且不另起炉灶——复用 PAT 的三层权限/审计/范围链路。

## 决策

### 1. `ApiApplication` = 「应用发卡机」，复用 PAT 认证链

```
ApiApplication（应用）
  ├─ client_id（唯一）/ client_secret_hash（sha256，复用 hash_pat_token 口径）/ token_prefix
  ├─ scopes（JSON 清单，与 PAT 同语义：允许的接口路径前缀/正则；空 = 不限）
  ├─ ip_allowlist（与 PAT 同语义）/ rate_limit_per_minute（0 = 不限）
  ├─ callback_urls（JSON 清单，https）/ is_active / expired_at / owner(creator)
```

- **换发**：`POST /api/system/open/token`（client_id + client_secret，JSON 或 Basic 头）→
  校验通过后为该应用**创建一条 PersonalAccessToken**（creator = 应用 owner，name = `app:<client_id>`，
  scopes/ip_allowlist/expired_at 从应用下发），返回 `{access_token, expires_in}`；
  同应用同 hashes 的存量有效凭证复用（幂等），不每次刷库；
- **认证零改动**：应用凭证走既有 `Authorization: Pat <token>` + `PersonalAccessTokenAuthentication`
  ——三层权限/数据权限/审计（`OperationLog.auth_type=pat`）全链路天然生效；
- **范围**：统一权限层既有 `resolve_pat_scopes` + `PatScopePermission` 直接消费
  （应用凭证的 `request.pat_scopes` 即应用 scopes），不给应用另开绕过路径；
- **限流**：`ApiApplicationThrottle`（DRF throttle）按 `token.pk` + 应用 `rate_limit_per_minute`
  走缓存计数，超限 429；PAT 手工凭证不带该字段，行为不变。

### 2. 回调注册（雏形边界）

- `callback_urls` 仅登记 https 回调端点；`POST /api/system/open/applications/{pk}/test-callback/`
  用 **webhook 既有 HMAC-SHA256 时间戳签名**（ADR-022 同一工具）向每个回调发一次探测 payload，
  返回逐 URL 的投递结果——不复用 webhook 的重试/审计链（雏形不做）；
- 明示不做：回调事件订阅模型（与出站 webhook 的关系在「开放平台二期」统一评估）、OAuth 授权码模式。

### 3. 管理面

- `ApiApplicationViewSet`（超管或 `*:*ApiApplication` 权限码）：CRUD + `regenerate-secret`
  （旧凭证即时失效 = 置 is_active=False）+ `test-callback`；secret 仅创建/重置时明文返回一次；
- 前端：`集成管理 → API 应用`（RePlusPage 三件套 + 两个自定义 action），菜单与权限码入种子。

### 4. 明示不做（雏形边界）

- 应用级独立权限体系（应用 = owner 的权限面，不另设角色）；
- 凭证轮换自动化 / jwks / OAuth 授权码与 refresh_token；
- 应用级审计报表（`OperationLog.auth_type=pat` 已可按 `token_pk` 回溯）。

## 实现落地记录（2026-09-13）

- `system/models/token.py`：`ApiApplication` + `PersonalAccessToken.api_application`（迁移 `0021`）；
- `system/views/open.py`：换发端点（凭据失败显式 401——无认证类的视图抛 `AuthenticationFailed` 会被 DRF 归一为 403）、
  `ApiApplicationViewSet`（create 返回一次性 secret + `regenerate-secret` 轮换 + `test-callback`）；
- `common/core/auth.py`：`check_api_application_rate_limit` + 认证处校验应用停用/过期；
- 种子：页面菜单 + 7 个权限码（list/retrieve/create/partialUpdate/destroy/regenerateSecret/testCallback）；
- 前端：`集成管理 → API 应用`（管理页 + 一次性密钥弹窗 + 回调测试）；白名单 `^/api/system/open/token$`。

## 验收（已执行）

- 集成用例：换发（正确/错误 secret/停用应用/过期应用）、凭证走 PAT 认证链（scopes 生效、
  审计 auth_type=pat）、per-app 限流 429、回调测试（HMAC 头可验签）、regenerate 后旧凭证 401；
- E2E：管理页主链路（建应用 → 复制 secret → 测试回调）；
- 门禁：pytest / ruff / i18n po / 前端四门禁 / E2E。
