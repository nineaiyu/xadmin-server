# ADR-018：企业 IM 扫码登录（钉钉 / 企业微信 / 飞书）

- 状态：已接受
- 日期：2026-09-12
- 关联：年度开发计划 2026.10-2027.09 §四 W2（G2a）；ADR-017（LDAP，本地目录的
  另一半）；既有身份联邦（OAuth2/OIDC 通用 provider，无独立 ADR，协议见
  `system/utils/oauth.py` 模块注释与 `docs/architecture/scim.md` 同期文档）

## 背景

身份联邦已有通用 OAuth2/OIDC provider（`OAUTH_PROVIDERS` 配置驱动：state 一次性、
`UserOAuthBinding` 绑定唯一、`complete_login` 收口 MFA、解绑防自锁），前端登录页
入口 / 回调落地页 / 个人绑定页均为 provider 无关的通用实现。

钉钉、企业微信、飞书三家国内 IM 的扫码登录是「最广诉求、复用度高」的接入项
（计划 G2a），但三家协议都**不是标准 OAuth2**，无法仅靠现有配置接入：

| 差异点 | 钉钉 | 企业微信 | 飞书 |
|--------|------|----------|------|
| 授权端点参数 | client_id 可用（AppKey） | `appid`=corpId + `agentid`，无 response_type | `app_id`（非 client_id） |
| 换 token | POST **JSON** clientId/clientSecret/grantType | 先 `gettoken`（corpId+Secret，有频控需缓存）再 `auth/getuserinfo` 一步取身份 | POST **JSON**（v2 token 端点，标准形状） |
| 用户信息 | `contact/users/me`（unionId/nick） | `user/get`（userid→name/email，需上一步 userid） | `authen/v1/user_info`（**data 包裹**，union_id） |
| 错误语义 | HTTP 状态码 + 报文体 | `errcode/errmsg` 包裹 | `code/data` 包裹 |

## 决策

### 1. provider 协议加 `flavor` 维度，通用 oauth2 流零改动

provider 配置新增 `flavor` 字段（默认 `oauth2` = 现行为），分发点只有三个纯函数：
`build_authorize_url` / `exchange_code` / `fetch_userinfo`。既有通用 provider 与
测试完全不受影响。

- `fetch_userinfo` 签名从 `access_token: str` 改为接收 `token_payload: dict`
  （内部 API，仅回调一处调用）：企业微信的身份在换码那一步已经拿到（`userid`），
  两步式协议下第二步需要同时携带 corp token 与 userid；
- 三家适配器收口在 `system/utils/oauth_flavors.py`（与 `oauth.py` 同 app），
  依赖注入 http 客户端，单测完全离线；
- 适配器返回**归一化后的 userinfo**（原始报文 ⊕ `nickname`/`email`/`picture`
  标准键）：`resolve_subject`（按配置的 `subject_field`）与
  `_create_user_and_binding`（建号取昵称/邮箱/头像）无需感知 flavor。

### 2. 内置 flavor 预设：管理员只填应用三元组

每 flavor 内置端点/字段预设（authorize/token/userinfo URL、默认
`subject_field`/`scope`），**读取时**（`get_providers`）合并默认值——管理员配置
只需 `key/name/flavor/client_id/client_secret`（企业微信另需 `agent_id`）+
`enabled`。预设端点写死官方域名常量，配置里仍可显式覆盖 URL（私有化网关场景）。
`validate_providers` 按 flavor 校验必填项与 https（只校验显式配置的 URL），
错误在**保存时**挡住（沿用既有口径）。

### 3. 企业微信 corp token 进缓存；错误一律可读掩码

- 企业微信 `gettoken` 有频控且有效期 7200s：corp token 以
  `sha256(corpId+secret)` 为 key 写 django cache（TTL 取 `expires_in - 120`），
  改密自动换 key 不复用旧 token；
- 三家错误处理与既有纪律一致：IdP 原始报文只进日志，用户侧统一可读文案
  （`OAuthError`）；`errcode/code` 非 0、缺 `access_token`、缺 subject 均映射为
  拒绝/联系管理员两类文案。

### 4. 登录链路与前端零新增面

- 回调仍走 `OAuthCallbackAPIView`：state 一次性 → 换码 → 取用户 → 绑定判定
  （`(provider, subject)` 唯一）→ `complete_login`——**MFA / 会话登记 / 登录日志
  对三家 IM 登录自动生效**（用例钉死）；
- `auto_create` 建号、解绑防自锁、`provider_subject` 用户名规则（防命名劫持）
  全部沿用；登录页入口/绑定页由既有通用组件按 provider `name` 渲染，
  providers 列表接口附 `flavor` 供前端未来做品牌图标，本期前端无布局改动；
- 三家以 `dingtalk` / `wecom` / `feishu` 为推荐 key（与 flavor 一致），
  绑定数据按 provider key 隔离。
- **补充（2026-09-14，个人中心补齐绑定入口）**：此前绑定只能由 IdP 侧
  `auto_create` 建号或既有绑定关系产生，「个人中心 → 第三方账号」只有解绑、无绑定入口。
  现补 `OAuthBindAuthorizeAPIView`（已登录，`/{provider}/bind-authorize`）+ 回调内
  `_bind_identity`：state 走独立键空间 `oauth_bind_state_{state}`（载荷含发起人 pk，
  与登录 state 互不可用），回调命中绑定意图时只建绑定**不下发 token**（不登录），
  归属校验以「载荷 pk == 当前登录用户」收口（防把 IdP 身份绑给他人/他人身份绑到本人），
  同一 `(provider, subject)` 已绑他人一律拒绝、已绑本人幂等返回 `already`。
  redirect_uri 与登录一致（同一落地页按 state 分流），管理员无需新增配置。

## 后果

- **明示不做**（登记边界）：企业微信第三方应用登录（login_type=ThirdApp，仅做
  企业自建应用 CorpApp）；钉钉旧版 sns 域名（`oapi.dingtalk.com`，统一走新
  `api.dingtalk.com` v1.0 端点）；飞书 v1 token 端点（统一 v2）；通讯录增量
  同步（属 G2b IM 消息渠道之外的组织架构同步，登记候选池）；三家内免登 JSAPI。
- **失败面**：任一 IM 故障仅影响该入口登录，回调统一可读文案；`enabled=False`
  或配置缺失的 provider 不出现在登录页（既有 `get_providers(enabled_only)`）。
- **安全**：client_secret 只存 SystemConfig 服务端可见，回传一律掩码；state
  跨 provider 不可重放（state 与 provider 绑定 + 一次性）；无密码账号
  auto_create 与 OAuth 通用口径一致，杜绝本地口令爆破面。

## 测试与验收

- 单元（离线 stub http）：三家 build_authorize_url 参数形状 / exchange 请求体
  与响应解析（含 errcode/code 包裹、data 包裹）/ userinfo 归一化（subject +
  nickname/email/picture）/ 企业微信 token 缓存命中与改密换 key / 错误掩码；
- 集成：flavor 配置校验（缺 agent_id、非 https、启用缺 secret）保存时拒绝；
  回调链路绑定唯一 / auto_create / 已禁用账号拒绝 / **MFA 回归**（三家 IM 登录
  命中 MFA 时返回 mfa_required）/ 解绑防自锁；
- E2E：种子注入启用态飞书 flavor provider → 登录页出现该入口按钮（providers
  接口 + flavor 默认值合并全链路）；全量 E2E 回归确认登录页改动零影响；
- 门禁：后端 pytest / ruff / i18n po；前端 typecheck / eslint / vitest。
