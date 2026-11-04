# ADR-101：登录态 token 承载双模式（HttpOnly Cookie 开关化，取代 ADR-099）

- 状态：**已交付**
- 日期：2026-11-03
- 关联：**取代 [ADR-099](ADR-099-http-only-refresh-token.md)**（触发条件①「安全评审要求长期凭据不可被
  JS 读取」由总审查 SEC-M1 项命中并触发本决策落地）、
  [ADR-001](ADR-001-csrf-jwt-only.md)（JWT-only 架构下的 Cookie 通道 Origin 兜底）、
  [ADR-100](ADR-100-review-retained-decisions.md)、
  `identity/services/auth_cookie.py`、`packages/xadmin-common/common/core/auth/authentication.py`、
  `xadmin-client/src/utils/auth.ts`

## 一、背景

ADR-099 登记的残余窃取面（access/refresh 均为 JS 可读 Cookie，XSS 绕过净化/CSP
防线后可读取并外带，refresh 可续 15 天）被 2026-11-03 总审查列为收尾修复项
（SEC-M1）。与 ADR-099 §四的候选轮廓相比，落地设计做了两处修订：

1. **access 与 refresh 都迁 HttpOnly Cookie**（ADR-099 只计划迁 refresh、access 留
   内存 + Authorization 头）——本项目 WS / SSE 无法自定义请求头，本就依赖
   X-Token Cookie 兜底认证；access 留「内存 + 头」会保留两套并行承载，收益
   不抵复杂度；HttpOnly 化后浏览器 JS 对两条 token 均不可读，SPA 全部请求改由
   浏览器自动携带 Cookie；
2. **服务端响应体剥离 token 明文**（ADR-099 未涉及）——只做 Cookie HttpOnly 而
   保留响应体 token，XSS 仍可从登录/刷新响应中截获明文，持久窃取面只关一半。

## 二、决策：开关化双模式，分阶段上线

**`AUTH_COOKIE_HTTPONLY`（默认 false）**：关闭 = 现状零变化（token 在响应体、
前端写 JS Cookie、拼 Authorization 头）；开启 = 服务端在登录 / 注册 / MFA 验证 /
刷新 / 模拟换签响应统一签发 `HttpOnly; SameSite=Lax; Secure(HTTPS)` 的
`X-Token` / `X-Refresh-Token`，并把响应体 `access` / `refresh` 置空串（键保留）。

**判定来源（不靠猜）**：服务端在**每次** token 签发响应同时下发 JS 可读的
`X-Auth-Mode` 标记 Cookie（值 `cookie` / `header`，两模式都下发——开关回退后
旧标记不残留误导）；`GET /api/identity/auth/capabilities` 的 `auth_transport`
字段是同一开关的只读投影。前端以标记 Cookie 为运行时判定（`getAuthTransport`）。

**安全口径**：

- Cookie 通道写请求的服务端 CSRF 兜底沿用认证类的 Origin/Referer 校验
  （`enforce_cookie_csrf` 模块级统一实现）；刷新端点凭 Cookie 取 refresh 时显式
  执行同一判定（跨站源 403）；
- 刷新端点双读：body refresh 优先，缺失回落 `X-Refresh-Token` Cookie（回读不设
  开关，回退窗口的存量会话无缝过渡）；轮换开启（`ROTATE_REFRESH_TOKENS`）下
  新 refresh 必须随响应重下 Cookie；
- 登出无条件清理三条 Cookie（含标记）并把 Cookie 中的 refresh 拉黑；Header
  （Bearer）/PAT 认证不受开关影响（双读兼容，脚本调用零变化）。

## 三、分阶段上线与回滚

1. **先发服务端**（开关默认关）：双读/清理逻辑就位，行为零变化；
2. **再发前端**（双模式判定就位）：`X-Auth-Mode` 标记缺失时按 header 模式，
   兼容旧版服务端；
3. **最后打开开关**：`config.yml`（重启生效）或系统配置页 `AUTH_COOKIE_HTTPONLY=true`
   （Setting 表热更，`settings.AUTH_COOKIE_HTTPONLY` 经 FORWARD_KEYS 转发，免重启）。
   **注意**：打开后仍持有旧版 bundle 的标签页在 token 过期后需整页刷新一次才能
   登录（旧代码读不到响应体 token），建议发布窗口执行或强制重新登录；
4. **回滚**：开关关回 `false` 即恢复响应体下发；存量 HttpOnly Cookie 会话在
   token 有效期内由双读继续可用，自然衰减。

## 四、后果

- **收益**：浏览器端 token 全部 HttpOnly，XSS 无法从持久化存储或登录/刷新响应
  读取明文——SEC-M1 的「脚本读 Cookie 外带」路径整体关闭；CSP / 净化链 /
  SameSite 等既有缓解继续保留不放松。
- **代价**：前端 token 不可读后，登录态判定、无感刷新、路由守卫全部改为
  「标记 Cookie + 401 策略驱动刷新」的口径（`hasSession` / 401-40001 Cookie 分支）；
  请求构造增加 `withCredentials`（跨域部署需服务端 CORS 允许凭据）。
- **边界**：与 header 模式并存期间，旧 JS Cookie 会话在下一次刷新时被服务端
  Cookie 原位替换（同名同路径），自动收敛到 Cookie 模式；access 过期后登出
  依赖下一次刷新轮换（与 header 模式的既有边界一致）。
- 实现与验证明细见工作区根 `docs/audit/2026-11-03-remaining-fixes.md`（服务端
  单测 `tests/unit/identity/test_auth_cookie_session.py` + 真机 curl 验证记录）。
