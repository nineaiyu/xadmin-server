# xadmin 开放平台接入指南

面向第三方接入方（ADR-030 一期 + ADR-039 二期）。本文是接入的**唯一入口文档**，
事件字段契约见 [events.md](./events.md)（自动生成）。

## 1. 概览

| 接入方式 | 适用场景 | 凭证 | 身份 |
|---|---|---|---|
| **个人访问令牌（PAT）** | 个人脚本 / 临时联调 | `个人中心 → 访问令牌` 自建 | 自然人（你自己） |
| **client-credentials（应用）** | 服务端 ↔ 服务端集成 | `client_id` + `client_secret` | 应用 owner（创建者） |
| **OAuth 授权码** | 「代表某位 xadmin 用户」访问 | 授权码 / refresh_token | 授权用户本人 |

三条链路的凭证统一走 `Authorization: Pat <token>` 头，权限判定完全一致。

## 2. 快速开始（client-credentials）

1. 平台管理员在 `集成管理 → API 应用` 创建应用，登记：
   - **接口范围（scope）**：可勾选到具体接口（如 `GET ^/api/system/user/?$`），空 = 不限；
   - **资源授权（可选）**：模型 × 动作 × 字段 × 行四级收敛（见 §3.2）；
   - **每分钟限流 / 每日配额**：见 §5；
   - **回调地址**：`https` 强制（联调可用 `http://127.0.0.1:*`）。
2. 创建成功时页面展示一次性 `client_secret`（**只展示一次**，丢失需重置密钥）。

```bash
# 换发凭证（轮换语义：旧凭证即时失效，请自行缓存）
curl -X POST http://<host>/api/system/open/token \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"app_xxx","client_secret":"aps_xxx"}'
# → {"code":1000,"data":{"access_token":"apst_xxx","token_type":"Pat","expires_in":7200,"scopes":[...]}}

# 调用接口
curl http://<host>/api/system/userinfo -H 'Authorization: Pat apst_xxx'
```

可直接运行的端到端演示：`python scripts/open_platform_demo.py --base-url ... --client-id ... --client-secret ...`

## 3. 权限模型（只收敛不提权）

应用凭证的最终权限面 = 下列三层**交集**，任何一层都不能把权限放大回 owner 全量：

```
应用 scope（接口路径正则）          ← 「能调哪些接口」
  ∩ 应用资源授权（四级，可选）        ← 「能操作哪些模型/动作/字段/行」
  ∩ owner（或授权用户）自身权限      ← 菜单权限 / 字段权限 / 数据权限
```

### 3.1 scope（接口级）

- 条目形如 `GET ^/api/system/user/?$`（METHOD + 锚定正则），空清单 = 不限制；
- 超出 scope → `403`（`PAT scope does not allow this path`）。

### 3.2 资源授权（模型 × 动作 × 字段 × 行）

在 `API 应用 → 编辑 → 资源授权` 配置；**不配置 = 兼容模式**（沿用 owner 权限 + scope），
配置任意一条即进入**白名单模式**：

| 层级 | 语义 | 违规结果 |
|---|---|---|
| 模型 | 请求目标模型必须被某条规则覆盖（`model` 精确或 `*`） | 403 |
| 动作 | 请求动作段（`list`/`retrieve`/`create`/`partialUpdate`/`destroy`…）必须在 `actions` 内（或 `*`） | 403 |
| 字段 | 规则 `fields` 非空时收敛字段（输出裁剪 + 输入拒绝未授权字段；空 = 全部字段） | 字段从响应中消失 / 写入被拒 |
| 行 | 规则 `row_filter` 非空时按数据权限同源规则过滤行（如「仅本人提交的数据」） | 列表/详情只返回命中行 |

约束说明：

- 四级对**超管 owner 同样生效**（约束挂在应用凭证上，不随用户身份豁免）；
- `model="*"` 的规则不支持字段/行级（通配模型下两者语义不成立）；
- 行级与字段级**不会放宽**用户自身的数据/字段权限，只做进一步收紧。

## 4. OAuth 授权码（代表用户访问）

```
第三方                        浏览器（被授权用户）              xadmin
  │  1. 跳转 /oauth/authorize?client_id&redirect_uri&scope&state&code_challenge(S256)
  │ ────────────────────────────────▶ 同意页（登录态）
  │                                    2. POST /api/system/open/oauth/approve (approved=true)
  │                                    3. 跳回 redirect_uri?code=xxx&state=yyy
  │  4. POST /api/system/open/oauth/token (grant_type=authorization_code, code, code_verifier)
  │  ◀── access_token(PAT) + refresh_token
  │  5. Authorization: Pat <access_token>
```

- `redirect_uri` 必须与应用的 `callback_urls` 完全一致；
- `scope` 可选（省略 = 应用全部 scope）；xadmin 的 scope 条目含空格，故用**逗号**分隔；
- PKCE：提供 `code_challenge` 后换发必须带 `code_verifier`（`S256` 或 `plain`）；
- 授权码一次性、5 分钟有效；refresh_token 一次即轮换（刷新后旧 refresh 与旧 access 同时失效）；
- 撤销：`POST /api/system/open/oauth/revoke`（`token` 传 refresh 或 access 均可）；
- access 的身份是**授权用户**，权限面 = 应用 scope × 应用资源授权 × 该用户权限（交集）。

错误响应统一为 `{"code":1001,"detail":"...","data":{"error":"<标准 OAuth 错误码>"}}`
（`invalid_client` / `invalid_grant` / `invalid_scope` / `unsupported_grant_type` / `invalid_request`）。

## 5. 限流与配额

| 机制 | 触发 | 响应 |
|---|---|---|
| 每分钟限流（`rate_limit_per_minute`） | 按应用计数，超限 | `429`（凭证仍有效，下分钟恢复） |
| 每日配额（`daily_quota`，软） | 达 `quota_alert_percent`（默认 80%） | 站内信（超管）+ `api_quota.warning` 事件；**不阻断** |
| 凭证级限流（PAT 速率） | 系统/个人配置 | `429` |

## 6. Webhook（出站事件）

- 在 `集成管理 → Webhook 订阅` 登记 URL 与事件，密钥用于签名（只展示一次）；
- 请求头：

| 头 | 说明 |
|---|---|
| `X-Xadmin-Event` | 事件 key（如 `user.login_succeeded`） |
| `X-Xadmin-Delivery` | 投递主键（幂等键，重试保持不变） |
| `X-Xadmin-Timestamp` | Unix 秒 |
| `X-Xadmin-Signature` | `sha256=HMAC(secret, "{timestamp}.{raw_body}")` |

- 重试：最多 5 次，退避 `min(60 × 2^(attempt-1), 3600)` 秒；耗尽后订阅行记录
  `last_failure` 并发站内信告警，可在「投递审计」页手动重试；
- payload 外壳 `{"event","schema_version","occurred_at","data"}`，`data` 字段契约见
  [events.md](./events.md)；建议校验时间戳窗口（±5 分钟）防重放；
- 本地验签演示：`python scripts/open_platform_demo.py --receive-webhook --webhook-secret <密钥>`。

## 7. 故障排查

| 现象 | 常见原因 |
|---|---|
| `401 Invalid client credentials` | client_id/secret 错误，或应用被重置密钥（旧 secret 即时失效） |
| `401 Token is invalid or expired` | 应用被停用/过期、owner 账号停用，或凭证已被新一次换发轮换掉 |
| `403 PAT scope does not allow this path` | 接口不在应用 scope 内 |
| `403 API application grant does not allow this operation` | 应用配置了资源授权，但该模型/动作未授权 |
| `403 Permission denied`（菜单权限） | owner/授权用户自身没有该接口的菜单权限（请先确认用户身份可访问） |
| `429` | 触发按应用限流或凭证限流 |
