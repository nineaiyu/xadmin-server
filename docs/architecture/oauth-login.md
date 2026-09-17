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
| `flavor` | 否 | `oauth2`（默认）/ `dingtalk` / `wecom` / `feishu`；IM flavor 走官方端点预设 |
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