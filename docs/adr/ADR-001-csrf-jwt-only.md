# ADR-001: CSRF 中间件保持禁用（JWT-only 认证架构）

- 状态：已接受（2026-09-04）
- 关联：半年规划 T1.6 / TD-06

## 背景

`server/settings/base.py` 中 `CsrfViewMiddleware` 自框架初始版本即被注释。此前无决策记录，存在两种误用风险：误以为漏配而启用（破坏现有客户端），或误以为不安全而盲目保持。

## 决策

**保持禁用**，理由：

1. 认证完全基于 JWT（Authorization Bearer / SimpleJWT 双 Token），不使用 Cookie Session 认证，CSRF 攻击的核心前提（浏览器自动携带的跨站凭据）不存在；
2. Token 存取由前端 `src/utils/auth.ts` 显式管理（js-cookie + 同站点请求头注入），非浏览器自动行为；
3. 跨站防护由 CORS 白名单（`CORS_ALLOWED_ORIGINS` 配置化）+ `RefererCheckMiddleware` + `XFrameOptionsMiddleware` 分层承担。

## 约束（保持禁用的前提，破坏任意一条需重新评估）

- 不得引入 Cookie-based 会话认证；
- 前端不得将 JWT 持久化到会被跨站自动携带的路径（当前 js-cookie 仅为同请求显式读取注入 header，属可控）；
- 若未来启用 SessionAuth 或 Admin 站点（`django.contrib.admin` 依赖 CSRF），必须恢复该中间件并为 API 路由豁免。

## 后果

- 正面：消除框架维护者对该配置的反复疑虑（本文档即记录）；
- 负面：若上述约束被无意破坏，防护失效——在 CI 中加静态断言（settings 检查）可作为后续加固项。
