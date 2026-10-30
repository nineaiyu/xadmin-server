# 开放平台控制台（SCIM / 第三方登录 / 已授权应用）

> 页面：`系统设置 → 开放平台`（`/settings/open-platform`）
> 账户侧：`账户设置 → 已授权应用`（`/account-settings?tab=authorizedApps`）
> 相关：[SCIM 2.0 目录同步](scim.md) · [第三方登录与 IM 扫码](oauth-login.md) · [开放平台接入指南](../open-platform/README.md)

## 1. 定位

开放平台的协议面（SCIM / OAuth2 / OIDC / PAT / API 应用）此前已完整，但缺少
产品化入口：SCIM 与第三方登录只能改 `SystemConfig` 原始 JSON，用户无法自助查看
和撤销已授权应用。本页把三类能力收口为可发现的页面，并保持**单一写路径**：

| 能力 | 入口 | 存储 | 写入 |
|---|---|---|---|
| SCIM 目录同步 | 系统设置 → 开放平台 → SCIM | `SystemConfig.SCIM_*` | typed 序列化器 + 既有 SystemConfig 保存服务 |
| 第三方登录 | 系统设置 → 开放平台 → 第三方登录 | `SystemConfig.OAUTH_PROVIDERS` | 同一份 `validate_providers` + 字段级加密 |
| 已授权应用 | 系统设置 → 开放平台 → 已授权应用（管理员审计）/ 账户设置 → 已授权应用（用户自助） | `OAuthRefreshToken` / `PersonalAccessToken` | 撤销动作（不新增配置写路径） |

## 2. SCIM 页签

- 字段：`SCIM_ENABLED` / `SCIM_TOKEN` / `SCIM_RATE_LIMIT` / `SCIM_DEFAULT_ROLE_CODE`；
- `SCIM_TOKEN` 为 `write_only`：回显只给「是否已配置」，留空保存 = 沿用；
- 「生成/轮换令牌」由 `system.services.credential_rotate.issue_system_config_secret`
  生成随机值并加密落库，明文**仅本次响应返回**；
- 「校验配置」为本地自检（开关 / 令牌 / 限流格式 / 默认角色），读取库内最新值，
  避免多 worker 下 L1 缓存 30s 收敛窗口造成误报；SCIM 由 IdP 主动回调，平台侧
  没有可拨测的对端连接；
- 「最近同步记录」来自 `OperationLog.auth_type=scim`（最近 20 条，不含请求体）。

端点在 `settings/views/scim.py`：

| 方法 | 路径 | 权限码 |
|---|---|---|
| GET | `/api/settings/scim` | `retrieve:ScimSetting` |
| PATCH | `/api/settings/scim` | `partialUpdate:ScimSetting` |
| POST | `/api/settings/scim` | `create:ScimSetting`（自检） |
| POST | `/api/settings/scim/rotate-token` | `rotateToken:ScimSetting` |
| GET | `/api/settings/scim/sync-status` | `syncStatus:ScimSetting` |

## 3. 第三方登录页签

- 结构化编辑：每个 provider 卡片维护核心字段（名称 / key / flavor / client_id /
  client_secret / scope / subject_field / auto_create）；「高级配置」维护 issuer、
  discovery_url、三个端点、JWKS、组字段、claims 映射、组到角色映射（JSON）；
- 「原始 JSON」模式编辑完整 `OAUTH_PROVIDERS` 数组，应用后仍需保存；
- `client_secret` 留空 = 沿用已存值；提交前由后端按 `key` 合并旧密钥，再走
  `validate_providers` 与 `SystemConfigSerializer`（字段级加密 + 缓存失效）；
- 「测试 discovery」仅对 `flavor=oidc` 开放：经统一出站守卫拉取
  `issuer/.well-known/openid-configuration` 或显式 `discovery_url`，返回授权 / 换码 /
  JWKS / userinfo 端点与 scopes/claims；失败统一可读文案，不回显 IdP 原始报文。

端点在 `settings/views/oauth.py`：

| 方法 | 路径 | 权限码 |
|---|---|---|
| GET | `/api/settings/oauth/providers` | `retrieve:OAuthProviderSetting` |
| PATCH | `/api/settings/oauth/providers` | `partialUpdate:OAuthProviderSetting` |
| POST | `/api/settings/oauth/providers/test` | `test:OAuthProviderSetting` |

## 4. 已授权应用（管理员审计 / 用户自助）

- 聚合口径：`OAuthRefreshToken`（未撤销、未过期）+ `PersonalAccessToken`
  （`name` 前缀 `oauth:`，属主为授权用户），按应用一行；
- 行字段：应用名 / client_id、授权用户数（管理员）/ 授权范围、授权时间、最近使用、
  访问 / 刷新令牌数、应用状态；
- 管理员页签：查看全部用户的授权聚合，撤销 = 失效该应用的全部 OAuth 授权链；
- 用户页签：只看本人，撤销只失效当前用户的授权链；
- 撤销只动 OAuth 授权链（refresh + 关联 access + `oauth:` PAT），**不动应用 owner
  的 client-credentials 凭证**（`name` 前缀 `app:`）；应用停用 / 过期时仍展示状态并允许撤销；
- 端点在 `identity/views/user/authorized_app.py`：
  - 管理员：`GET /api/identity/authorized-applications`（`list:AuthorizedApplication`）、
    `POST /api/identity/authorized-applications/{id}/revoke`（`revoke:AuthorizedApplication`）；
  - 用户：`GET /api/identity/my-authorized-applications`、
    `POST /api/identity/my-authorized-applications/{id}/revoke`，走
    `PERMISSION_WHITE_URL`（登录态即可，无菜单权限点）。

## 5. 权限与种子

- 页面 `SettingOpenPlatform` 挂 `系统设置` 下；10 个权限点的后缀按视图集拆分：
  `ScimSetting`（retrieve / partialUpdate / create / rotateToken / syncStatus）、
  `OAuthProviderSetting`（retrieve / partialUpdate / test）与
  `AuthorizedApplication`（list / revoke）；
- 权限点由 `sync_menu_permissions --update-seed` 按 `PARENT_MENU_MAP` 的
  `(后缀, 父菜单)` 归属生成；管理员角色授权在 `settings/seeds/grants.json` 同步声明；
- 新增端点必须同步：菜单种子 → `sync_menu_permissions --dry-run` 缺口 0 →
  `load_init_json --check` 0 error。

## 6. 安全与边界

- 密钥：SCIM token 整值加密；OAuth `client_secret` 字段级加密；读取侧只回掩码 / 布尔；
- 出站：OIDC discovery 与 OAuth 换码共用 `common/utils/outbound.py` 口径
  （https 强制、私网 / 环回 / link-local 拒绝、可登记放行）；
- 用户撤销不影响应用管理面；应用停用 / 密钥重置仍由 `ApiApplicationViewSet` 统一失效全部凭证；
- 管理员页签不展示全部用户的授权明细（避免在设置页引入第二套审计面）；需要审计时查
  `OperationLog` 的 OAuth 授权记录与 API 应用用量。

## 7. 验证

- 后端：`tests/integration/system/test_scim_settings.py`、
  `test_oauth_provider_settings.py`、`test_authorized_app.py`；
- 前端：`src/views/settings/open-platform/utils/oauthProvider.spec.ts`、
  `src/views/account/utils/panes.spec.ts`；
- E2E：`e2e/open-platform.e2e.ts`（菜单可达、双页签渲染、账户页签可达）。
