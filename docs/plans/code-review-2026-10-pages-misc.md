# 2026-10 全栈功能审查 · 分册：其余页面（静态/匿名/个人 9 页）

> 主册：[code-review-2026-10.md](code-review-2026-10.md)。证据等级：A=代码直接确证，B=跨文件静态推断，C=待运行时验证。
> 本批多为匿名端点与个人凭证页；匿名端点的「无权限点」合法性已在主册 §3.1 全量核实（视图显式 `permission_classes = []`/`AllowAny`）。

## 1. 登录（/login）

- **标识**：identity · core_auth · `login/index.vue` + 组件（账密/验证码/MFA/Passkey 多形态）· 后端 `identity/views/auth/login.py`（BasicLoginAPIView / VerifyCodeLoginAPIView / MFA 三端点，匿名）。
- **后端实现**：`LoginThrottle` 挂全部登录入口（`login.py:68,144`）；MFA 412 协议（架构文档 auth-flows/mfa）；Passkey 挑战白名单化（settings/custom.py:60-61）；验证码端点 `identity/auth/captcha` 匿名（CaptchaAPIView）。
- **闭环校验**：权限 ✅（匿名设计 + 限流）；契约 ✅；数据 ✅（C：防枚举口径——统一错误文案与延时，架构文档声明，未逐行验证）。
- **问题清单**：无。

## 2. 注册 / 找回密码（/register、/reset —— 同属登录链路）

- **后端实现**：`RegisterThrottle`（`register.py:33`）、`ResetPasswordThrottle`（`reset.py:18`）；邀请/注册/找回共用 AES 密文令牌库（`AesEncrypted(token, password)`，同库同参序，`invite.py:14-15`）。
- **闭环校验**：权限 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 3. 邀请接受（/invite/accept）

- **标识**：identity · core_auth · `invite/index.vue` · 后端 `identity/views/auth/invite.py`（validate 预检不消费令牌 + accept 激活）。
- **后端实现**：令牌 AES 密文传输（前端提交 `AESCipherV2(token)` 密文）；解密失败/空明文受控拒绝不落 500（:14-15）；validate/accept 匿名。
- **闭环校验**：权限 ✅；契约 ✅；数据 ✅（令牌消费语义：预检不消费）。
- **问题清单**：无。

## 4. OAuth 回调 / 授权（/oauth/callback、/oauth/authorize）

- **标识**：identity · core_auth（第三方登录）+ open_platform（授权码）· 后端 `identity/views/auth/oauth.py`（providers/bindings/bind-authorize/callback，匿名+登录态混合）+ `open_oauth.py`（authorize/approve/token/revoke）。
- **后端实现**：白名单 `^/api/identity/auth/oauth/` 与 `^/api/identity/open/oauth/`（settings/custom.py:76-84，注释明确「authorize/callback 必须匿名可达、绑定管理是个人凭证、视图内自行 fail-closed」）；开放平台换发凭 client_secret 认证。
- **闭环校验**：权限 ✅（白名单 + 视图内收口声明）；契约 ✅；数据 ✅（C：state 防 CSRF 的运行时校验属架构文档声明面）。
- **问题清单**：无。

## 5. 个人中心（/user/info/index）

- **标识**：identity · core_auth · `account/index.vue` + 组件（资料/头像/安全设置）· 后端 `identity/views/user/userinfo.py UserInfoViewSet`（DetailUpdateModelSet + upload + reset-password + verify-password）。
- **后端实现**：verify-password 挂 `PasswordVerifyThrottle`（:128，锁屏解锁/敏感确认共用）；密码密文解密失败受控文案（`is_password_decrypt_failure`，:110,139）；`must_change_password` 标记随改密清除（:62-63）。
- **闭环校验**：权限 ✅（白名单 userinfo + 个人凭证族端点）；元数据 ➖（个人页）；契约 ✅；数据 ✅。
- **问题清单**：无。

## 6. 账号设置（/account-settings）

- **标识**：identity · core_auth · `account` 复用（remaining.ts:94）· 后端同个人中心 + MFA/PAT/Passkey 个人凭证端点（全部白名单个人自管，settings/custom.py:33-58）。
- **闭环校验**：权限 ✅（个人凭证三族：MFA/PAT/Passkeys 白名单 + 视图内收口本人）；数据 ✅。
- **问题清单**：无。

## 7. 欢迎页（/welcome）

- **标识**：静态欢迎页 + 组件 · 数据源 `api/system/dashboard/*`（白名单 `^/api/system/dashboard/`，结构元数据口径已文档化）。
- **闭环校验**：权限 ✅（白名单，统计口径不含明细行）；契约 ✅；数据 ✅。
- **问题清单**：无。

## 8. 关于页（/about/index）

- **标识**：静态页（版本信息展示）· 平台种子菜单。SFC 单根约定守护用例的基准页（e2e system-pages「关于页」，client AGENTS §五）。
- **问题清单**：无。

## 9. 错误页 / Playground（/error/*、/playground/*）

- **标识**：error 403/404/500 组件（静态路由 modules/error.ts）；playground 为开发试验场（modules/playground.ts，含 __tests__）。
- **问题清单**：无。
- **质量速记**：E2E 有 a11y/visual 基线覆盖 error 页（视觉结论移交 Phase 2）。

## 批次 8 小结

- **发现数**：P0=0 / P1=0 / P2=0 / E=0。
- **正向**：认证链四类限流器全覆盖（login/register/reset/verify-password）；令牌族统一 AES 密文库；匿名端点白名单纪律（每条白名单条目都有理由注释与对应视图内收口）。
- **已读文件**：`identity/views/auth/{login,register,reset,invite}.py`（关键段）、`identity/views/user/userinfo.py`（关键段）、`identity/views/auth/oauth.py` + `open_oauth.py`（路由/白名单面）；前端 login/invite/account 结构确认（静态路由 remaining.ts 全读）。
