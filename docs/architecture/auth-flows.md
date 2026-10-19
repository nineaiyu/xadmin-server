# 登录 / 注册 / 找回密码链路

> 本文是认证域（`identity/`）四条主链路的分步时序与收口约定。MFA 二次验证细节见
> [mfa.md](mfa.md)，第三方登录细节见 [oauth-login.md](oauth-login.md)，排障见
> [../guide/troubleshooting.md](../guide/troubleshooting.md)。
>
> 范围：账密登录 / 验证码登录 / 注册 / 找回密码 / 邀请激活 / 第三方登录 / 用户模拟。

## 一、两条收口约定（新增登录方式前必读）

认证链路历史上把「同一件事在每个入口各写一遍」做到开始漂移（签发 token 的五步
逻辑曾有 7 份拷贝且已不一致：账密登录不写 `last_login`、注册不带
`must_change_password`、降级 try/except 写法各不相同）。现在收敛为两个唯一收口：

| 收口 | 位置 | 约束 |
|------|------|------|
| **成败处置** | `identity/services/auth_login.py` 的 `complete_login` | 任何登录路径的成功后置链路（MFA 判定 / 登录日志 / 异地提醒 / 锁定计数清理）都必须经过它。绕过 = 绕过登录 MFA 与失败计数 |
| **token 签发** | `identity/services/token_issue_login.py` 的 `issue_session_token` | 任何签发「给人登录用」的 JWT 的路径都必须调它，不得自行拼接 `RefreshToken.for_user` / `bind_session_claim` / `get_token_lifetime` |

`issue_session_token` 一次性完成五步，并统一降级口径：

1. `register_user_session()` 登记 `UserSession`（失败仅告警，不阻断登录）；
2. `RefreshToken.for_user(user)` 签发；
3. `bind_session_claim(refresh, session.pk)` 写 `sid` claim（失败退回无 sid 令牌，不阻断）；
4. `get_token_lifetime(user)` 附 `access_token_lifetime` / `refresh_token_lifetime`；
5. 附 `must_change_password` 标记，并按 `touch_last_login` 显式刷新 `last_login`。

两个参数：

- `extra_claims`：额外 claim。用户模拟的 `impersonator` claim（发起人 pk）经此传入；
- `touch_last_login`：默认 `True`。注册 / 账密 / 验证码 / MFA / OAuth 均刷新；
  用户模拟传 `False`（模拟不是目标账号的真实登录）。

**例外（有意登记）**：退出用户模拟（`identity/utils/impersonation.py` 的
`stop_impersonation`）不经过本函数——那是「恢复发起人原有登录态」而非一次新
登录，复用发起人既有会话语义，有意不登记新会话、不写 `last_login`。

`last_login` 与 simplejwt 的 `UPDATE_LAST_LOGIN`：登录态签发已显式写入，
该开关（`server/settings/libs.py`）保留为 refresh 轮换路径的兜底，两者写同值。

## 二、链路时序

### 2.1 账密登录（`/api/identity/login/basic`）

```
前端 Basic.vue
  → POST /login/basic  {username, password, token, captcha_key, captcha_code}
后端 BasicLoginAPIView.post：
  1. SECURITY_LOGIN_ACCESS_ENABLED 开关判定（关闭即 1001）
  2. check_token_and_captcha：临时令牌 + 图形验证码（按开关）
  3. get_username_password：AES v2 解密（按 SECURITY_LOGIN_ENCRYPTED_ENABLED）
  4. check_is_block：IP 维度 + 账号维度锁定前置
  5. SessionTokenObtainPairSerializer.is_valid：纯校验（无副作用）
     失败 → login_failed()（失败计数 + 失败日志 + 通用文案，防枚举）
  6. evaluate_login_policy_for_request：登录访问策略（密码校验通过后判定）
     拒绝 → reject_login()（绑定用户归属 + 失败日志 + 可读文案）
  7. complete_login：MFA 判定
     需要 MFA → 返回 {mfa_required, mfa_token, methods}
  8. issue_session_token：登记会话 + 签发 + sid claim + 生存期 + 改密标记 + last_login
```

LDAP bind 认证成功时登录日志来源记 `LDAP`（`_login_type_for` 读
`user._ldap_authenticated`），其余记 `USERNAME`。

### 2.2 验证码登录（`/api/identity/login/code`）

```
前端 Login.vue
  → POST /auth/verify?category=login  发送验证码（防枚举口径，见 2.7）
  → POST /login/code  {verify_token, verify_code, password?}
后端 VerifyCodeLoginAPIView.post（GenericAPIView，匿名可达）：
  1. verify_sms_email_code：核验缓存验证码（失败计数 + 失败日志）
  2. query_key == "username"（手机/邮箱 + 密码组合）→ authenticate
     认证失败 → login_failed()
  3. evaluate_login_policy_for_request → reject_login()
  4. complete_login（本路径已过动态因子，短信/邮件验证码直登时不再走 MFA）
  5. issue_session_token → login_success
```

### 2.3 注册（`/api/identity/auth/register`）

```
前端 Register.vue
  → POST /auth/verify?category=register  发送验证码（目标已存在时明确告知）
  → POST /auth/register  {verify_token, verify_code, password}
后端 RegisterViewAPIView.post：
  1. SECURITY_REGISTER_ACCESS_ENABLED 开关
  2. verify_sms_email_code（RegisterBlockUtil）+ 密码规则 / 泄漏库校验
  3. ReentrantLock("register_user") 全局锁内查重（all_objects 口径，防软删同名撞唯一约束）
  4. create_user + record_password_hash（首条密码历史）
  5. channel 自动绑定部门（auto_bind）
  6. issue_session_token：注册即登录（同登录口径签发）
  7. save_login_log
```

### 2.4 找回密码（`/api/identity/auth/reset`）

```
前端 ResetPassword.vue
  → POST /auth/verify?category=reset  发送验证码（不区分账号存在性，防枚举）
  → POST /auth/reset  {verify_token, verify_code, password}
后端 ResetPasswordAPIView.post：
  1. verify_sms_email_code（ResetBlockUtil）+ AES 解密
  2. LDAP 绑定用户直接拒绝（密码由目录管理）
  3. 密码规则 / 泄漏库 / 历史密码三重校验
  4. set_password + record_password_hash + 作废 verify_token
```

重置成功不登录：前端返回账密登录页。

### 2.5 邀请激活

管理员预建账号 → 邮件带一次性邀请令牌 → 前端 `/#/invite/accept` 预检
（`state` = pending / accepted / invalid，不消费令牌）→ 设密码并激活。
激活后的 `must_change_password` 由 `issue_session_token` 随登录响应下发，
触发前端改密引导。

### 2.6 第三方登录 / 用户模拟

第三方登录的完整链路（authorize / state CSRF / callback / auto_create / 组角色
同步）见 [oauth-login.md](oauth-login.md)。与本文相关的约定：callback 登录成功
后同样必须走 `complete_login` + `issue_session_token`（登录日志来源 `OAUTH`）。

用户模拟：`start_impersonation` 经 `issue_session_token` 签发，带
`impersonator` claim（发起人 pk），登录日志归属被模拟账号、类型 `IMPERSONATE`；
`touch_last_login=False`（不污染目标账号 `last_login`）。退出模拟见 §一例外。

## 三、验证码场景开关矩阵

`GET/POST /api/identity/auth/verify?category=<key>` 由 `verify_code.py` 的
`CATEGORIES` 表驱动（新增场景只登记一行，`CATEGORY_KEYS` 与 swagger enum 均从
表派生）：

| category | access 开关 | 目标已存在时 | 投递通道 |
|----------|-------------|--------------|----------|
| `register` | `SECURITY_REGISTER_ACCESS_ENABLED` | **明确告知**（注册必须提示占用） | email/sms |
| `login` | `SECURITY_LOGIN_ACCESS_ENABLED` | 静默成功但不发送（同 reset） | email/sms |
| `reset` | `SECURITY_RESET_PASSWORD_ACCESS_ENABLED` | 静默成功但不发送（防枚举） | email/sms |
| `bind_email` | `SECURITY_BIND_EMAIL_ACCESS_ENABLED` | 回显目标用户资料（绑定到哪个账号） | email |
| `bind_phone` | `SECURITY_BIND_PHONE_ACCESS_ENABLED` | 同上 | sms |

辅助开关（各场景独立）：`SECURITY_<场景>_CAPTCHA_ENABLED`（图形验证码）、
`SECURITY_<场景>_TEMP_TOKEN_ENABLED`（临时令牌）、
`SECURITY_<场景>_ENCRYPTED_ENABLED`（AES v2 传输加密）、
`SECURITY_<场景>_BY_EMAIL_ENABLED` / `_BY_SMS_ENABLED`（叠加 `EMAIL_ENABLED` /
`SMS_ENABLED` 总开关）。投递通道由 `SendAndVerifyCodeUtil` 承载，限流见
`SendVerifyCodeBlockUtil` / `LoginIpBlockUtil`。

## 四、防枚举口径（安全相关，显式表达）

| 场景 | 是否区分账号存在性 | 原因 |
|------|---------------------|------|
| 注册 | **区分**（`Phone/Email/Username already exist`） | 注册流程必须提示占用，否则用户无法完成注册 |
| 登录 / 重置 | 不区分 | 账号不存在（或已停用）时不发送验证码，但**支付同等的模板渲染开销**并返回完全一致的响应文案与结构；后续验证码校验必然失败，攻击者无法据此判断账号是否存在 |
| 绑定 | 区分 | 绑定流程需要展示「绑定到哪个账号」，且调用方已登录 |

该口径在代码里的位置：`check_register_config`（区分）与 `check_reset_config`
（不区分）；`CATEGORIES` 表中 `login` 与 `reset` 共用同一个 check 函数，
「登录与重置同口径」是显式登记，不再靠转发方法隐藏。

## 五、前端结构（xadmin-client）

登录子页在 `src/views/login/`，四类表单页共用一套组合式函数与组件：

| 共享件 | 位置 | 职责 |
|--------|------|------|
| `useLoginFlow` | `useLoginFlow.ts` | 登录成功后的单一路径（提示 / 改密引导 / 动态路由 / redirect） |
| `useAuthInfo` | `utils/useAuthInfo.ts` | 后端配置（`AuthInfoResult["data"]`）的合并状态，四页同形状 |
| `useVerifyCodeSubmit` | `utils/useVerifyCodeSubmit.ts` | 「验证码表单 → 本表单 → 提交」的共享校验链（含 ref 空值保护） |
| `useRememberLogin` | `utils/useRememberLogin.ts` | 记住我勾选与免登录天数状态 |
| `RememberLogin.vue` | `components/` | 记住我模板（勾选 + 天数 select + 提示图标，均带 aria-label） |
| `PasswordFields.vue` | `components/` | 密码 + 确认密码表单项（注册 / 重置共用） |
| `loginShared.ts` | `utils/` | 载荷拼装（`buildVerifyCodePayload`）、密码校验规则（`createPasswordFormRules`） |
| `encryptCredential` | `src/utils/aes.ts` | 凭证加密唯一入口（账密登录与验证码类载荷共用） |

错误处理口径：`handleOperation`（成功 / 失败提示 + `requestEnd` loading 收口），
业务失败一律页面显式处理；路由层失败（动态拉取失败）走 `initRouter().catch`
跳静态 500 页。

## 六、相关测试

| 面 | 测试 |
|----|------|
| 账密 / 验证码登录边界 | `tests/integration/system/test_auth_api.py`、`test_login_edge.py` |
| 注册 / 重置 / 邀请 | `test_register_api.py`、`test_password_reset_api.py`、`test_user_invite.py` |
| 验证码场景表 | `test_verify_code_api.py` |
| 登出 / 登录策略 / MFA | `test_logout_config.py`、`test_login_policy.py`、`test_mfa_api.py` |
| 第三方 / 模拟 | `test_oauth_authorize.py`、`tests/unit/system/test_impersonation.py` |
| 前端登录流 | `src/views/login/**/*.spec.ts`（useLoginFlow / useAuthInfo / useVerifyCodeSubmit / RememberLogin / PasswordFields） |
| E2E | `e2e/auth.e2e.ts`、`e2e/reset-password.e2e.ts`、`e2e/verify-code-resend.e2e.ts`、`e2e/a11y.e2e.ts`、`e2e/visual.e2e.ts`（login 基线） |
