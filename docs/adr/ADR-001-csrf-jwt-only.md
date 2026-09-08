# ADR-001: CSRF 中间件保持禁用（JWT-only 认证架构）

- 状态：部分修订（2026-09-06，SEC-1）——原决策约束第 3 条被触发，`CsrfViewMiddleware` 已恢复启用
- 关联：半年规划 T1.6 / TD-06；优化升级开发文档 SEC-1

## 背景

`server/settings/base.py` 中 `CsrfViewMiddleware` 自框架初始版本即被注释。此前无决策记录，存在两种误用风险：误以为漏配而启用（破坏现有客户端），或误以为不安全而盲目保持。

## 原决策（2026-09-04）

**保持禁用**，理由：

1. 认证完全基于 JWT（Authorization Bearer / SimpleJWT 双 Token），不使用 Cookie Session 认证，CSRF 攻击的核心前提（浏览器自动携带的跨站凭据）不存在；
2. Token 存取由前端 `src/utils/auth.ts` 显式管理（js-cookie + 同站点请求头注入），非浏览器自动行为；
3. 跨站防护由 CORS 白名单（`CORS_ALLOWED_ORIGINS` 配置化）+ `RefererCheckMiddleware` + `XFrameOptionsMiddleware` 分层承担。

### 约束（保持禁用的前提，破坏任意一条需重新评估）

- 不得引入 Cookie-based 会话认证；
- 前端不得将 JWT 持久化到会被跨站自动携带的路径（当前 js-cookie 仅为同请求显式读取注入 header，属可控）；
- 若未来启用 SessionAuth 或 Admin 站点（`django.contrib.admin` 依赖 CSRF），必须恢复该中间件并为 API 路由豁免。

## 修订记录（2026-09-06，SEC-1）

技术审查发现：`/admin/` 站点实际已挂载（`server/urls.py`）且 `SessionMiddleware` 开启，上述前提第 3 条**已被打破**——Admin
的登录与全部 POST 操作处于无 CSRF 防护状态。

按原决策预设的恢复路径执行：

1. `CsrfViewMiddleware` 恢复启用（`server/settings/base.py`），位于 LocaleMiddleware 与 AuthenticationMiddleware 之间；
2. API 路由无需逐个豁免：DRF 对全部 APIView 施加 `csrf_exempt`，Bearer/Cookie JWT 路径行为不变，SessionAuthentication 自带
   `enforce_csrf`；
3. CI 增加静态断言测试（`tests/unit/server/test_settings_assertions.py`）：启用 `django.contrib.admin` 时禁止移除
   `CsrfViewMiddleware`，防止回归；
4. 防护分层调整：Admin 站点由 CSRF 中间件承担；API 继续由 CORS 白名单 + RefererCheckMiddleware + XFrameOptionsMiddleware
   承担。

## 后果

- 正面：消除框架维护者对该配置的反复疑虑（本文档即记录）；
- 负面：若上述约束被无意破坏，防护失效——已通过 CI 静态断言（见修订记录第 3 条）闭环。
