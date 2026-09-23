# 第三方登录与 IM 扫码登录（OAuth）

> 面向管理员：provider 配置字段、生效条件与常见配置错误排错（功能模块评估 §4.1「配置错误提示依赖文档」）。
> 设计决策见 [ADR-018](../adr/ADR-018-im-scan-login.md)（IM 扫码登录）与 [ADR-019](../adr/ADR-019-im-notify-channels.md)（IM 消息渠道，复用同一批 OAuth 绑定）；
> 代码：`system/utils/oauth.py`（读取/写入校验）、`system/utils/oauth_flavors.py`（IM flavor 官方端点预设）、`system/views/auth/oauth.py`（登录/绑定回调）。

## 一、配置位置与字段

系统配置 → 第三方登录（`SysConfig.OAUTH_PROVIDERS`，JSON 数组），每项字段：

| 字段 | 必填 | 说明 |
|------|------|------|
| `key` | 是 | provider 唯一标识（自由命名，不可重复） |
| `name` | 是 | 登录页按钮展示名 |
| `flavor` | 否 | `oauth2`（默认）/ `oidc`（标准 OIDC，见 §六）/ `dingtalk` / `wecom` / `feishu`；IM flavor 走官方端点预设 |
| `client_id` / `client_secret` | 启用时必填 | 企业应用三元组（secret 只写不回显） |
| `authorize_url` / `token_url` / `userinfo_url` | 通用 flavor 必填；IM flavor 有预设可不填 | 显式配置时必须 `https://` |
| `scope` / `subject_field` / `enabled` / `auto_create` | 否 | 默认 `openid profile email` / `sub` / `False` / `False` |

**IM flavor 只需填「应用三元组」**（授权/换码/用户信息端点由官方预设补齐）。

## 二、生效条件（「配置了但登录页没有」先看这三条）

1. 只有 `enabled=True` **且** `client_id` 非空的 provider 才会下发给登录页（`get_providers(enabled_only=True)`）；
2. 平台侧登记的回调地址必须是系统固定落地页：
   `https://<站点>/ 的 /#/oauth/callback?provider=<key>`（换码时校验同一地址，防伪造 redirect_uri 带走 code）；
3. IM 通知渠道（钉钉/企微/飞书）的收件账号来自用户**经对应 flavor 登录**留下的 `UserOAuthBinding`
   ——从未经该 IM 登录的用户收不到该渠道消息（warning 日志 `skip N user(s) without <flavor> binding`）。

## 三、保存时校验（写入侧挡住，不把错误留给用户）

| 报错文案 | 原因 |
|----------|------|
| Invalid OAuth providers configuration | 配置不是 JSON 数组 |
| Unknown OAuth provider flavor: xxx | `flavor` 不在预设集合（oauth2 / dingtalk / wecom / feishu） |
| OAuth provider is missing required fields: … | 缺必填（通用 flavor 需要三个 URL；IM flavor 按预设放宽） |
| Duplicate OAuth provider key: xxx | `key` 重复 |
| OAuth provider url must use https: token_url | 显式配置的 URL 非 `https://` |
| Enabled OAuth provider requires client_secret | 已启用但缺 `client_secret` |

## 四、常见现象排错

| 现象 | 处置 |
|------|------|
| 登录页不显示该 provider | 查 `enabled` 与 `client_id`（两者都要有）；确认保存无校验报错 |
| 平台侧报 redirect_uri 不匹配 | 平台登记地址须与系统固定落地页一致（见 §二.2） |
| 扫码后提示身份已被占用 | 唯一约束是「同一 IdP 身份（provider + subject）只能绑定一个本地账号」；换账号或先在原账号解绑 |
| IM 渠道发不出去 | 用户是否经该 IM 登录过（`UserOAuthBinding` 按 flavor 归集，provider key 可自由命名）；查 warning 日志 |
| 改凭据后 IM 渠道异常 | token / userid 缓存按凭据摘要隔离（改密自动换 key）；确认新 secret 已保存并生效 |

## 五、新增一个 flavor（内核扩展）

IM flavor 的协议差异全部收口在 `system/utils/oauth_flavors.py`，新增一个 flavor = 三步：

1. **登记预设**：`FLAVOR_PRESETS` 加官方端点与 `subject_field`（授权 / 换码 / 用户信息 URL，允许显式配置覆盖）；
   `FLAVOR_REQUIRED_KEYS` 加写入侧必填键（如企微额外要求 `agent_id`）；
2. **实现分发函数**（同文件）：`exchange_code_<flavor>`（换 token，返回 `token_payload`）、
   `fetch_userinfo_<flavor>`（取用户信息并按 `subject_field` 归一化 `nickname` / `email` / `picture` 标准键）；
   授权地址参数形状与标准 OAuth2 不一致时，在 `build_flavor_authorize_url` 内补分支（一致则返回 `None` 落回通用构造）；
3. **挂进分发入口**：`exchange_flavor_code` / `fetch_flavor_userinfo` 的 handler 映射表各加一行。

## 六、标准 OIDC（flavor=oidc）

对接标准 OIDC IdP（Keycloak / Auth0 / Entra ID 等）：代码在 `system/utils/oidc.py`（discovery / id_token 验签 / claims 映射 / 组角色同步）。

**配置字段**（在通用字段之外）：

| 字段 | 必填 | 说明 |
|------|------|------|
| `issuer` | 与显式端点二选一 | IdP issuer（如 `https://keycloak.example.com/realms/corp`），用于 discovery 与 `iss` 校验 |
| `discovery_url` | 否 | 显式覆盖 discovery 地址（缺省 `{issuer}/.well-known/openid-configuration`） |
| `authorize_url` / `token_url` | 否 | 显式端点（优先级高于 discovery）；两者都填时可完全不配 `issuer` |
| `jwks_uri` | 否 | 显式覆盖 JWKS 地址（缺省取 discovery 的 `jwks_uri`） |
| `groups_field` | 否 | 组 claim 名（默认 `groups`；字符串或数组均可） |
| `group_role_map` | 否 | 组 → 角色 code 映射（口径同 LDAP 组映射：**只增删映射内出现的角色**，映射为空不启用） |
| `nickname_claim` / `email_claim` / `phone_claim` | 否 | claims → 本地资料字段映射（默认 `name` / `email` / `phone_number`） |
| `subject_field` | 否 | 身份唯一标识 claim（默认 `sub`，建号用户名仍按 `provider_subject` 规则生成） |

**安全口径**：

- 端点与 JWKS 全部来自 **HTTPS discovery** 或管理员显式配置；
- `id_token` 必须经 JWKS 公钥验签，并校验 `iss` / `aud` / `exp` / `nonce`；
  算法白名单（RS/PS/ES 系列），拒绝 `none` / HS*（防算法混淆）；密钥轮换时自动强制刷新一次 JWKS；
- `nonce` 与一次性 state 绑定（授权下发、回调校验后即失效），防 id_token 重放；
- 组 → 角色同步发生在**登录链路**（绑定链路不触发），且只增删映射内角色，管理员手工授予的映射外角色不被触碰。

**排错**：

| 现象 | 处置 |
|------|------|
| 点击登录按钮提示"无法连接身份提供商" | discovery 不可达：确认 `issuer`/`discovery_url` 为 https 且容器可访问；`http://` 会被保存校验直接拒绝 |
| 回调提示"id_token 无效" | `aud` 是否是 `client_id`、`iss` 是否与 `issuer` 一致、时钟偏差、算法是否在白名单；JWKS 轮换由自动刷新兜底 |
| 回调提示"未返回 id_token" | IdP 的 scope 需含 `openid`（默认 scope 已含）；确认 token 端点返回 `id_token` |
| 登录后角色没变 | `group_role_map` 是否配置、组名是否命中（支持组名精确匹配或 `cn=<组名>,...` 的 DN 形态）、角色 code 是否存在且启用 |

安全纪律（与通用流一致）：IdP 原始报文只进日志、用户侧错误统一 `OAuthError` 可读文案、
http 客户端可注入（保证单测离线）；换码 / 取用户信息的缓存按凭据摘要隔离（参考企微 corp token 实现）。

> flavor 属内核扩展（改动面在 `system/utils/` 与写入校验白名单），建议先提 ADR 再落代码。