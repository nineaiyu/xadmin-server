# 部署与运维手册 · 安全响应头（CSP）（deployment-csp）

> 本文为《部署与运维手册》子页（§8 安全响应头：CSP）。
> 概览（本地开发与排查）见 [deployment.md](deployment.md)；容器部署见 [deployment-docker.md](deployment-docker.md)。

## 8. 安全响应头：CSP（S3）

Django 侧由 `django-csp 4.0` 生成策略，运行期模式由系统配置控制（默认**观察期**，不拦截请求）：

| 配置项 | 取值 | 说明 |
|---|---|---|
| `CSP_MODE` | `report-only`（默认）/ `enforce` / `disabled` | report-only 只下发 `Content-Security-Policy-Report-Only`；切 `enforce` 后同一策略串改为强制头 |
| `CSP_REPORT_URI` | 空（默认）/ `/api/common/api/csp-report` | 非空时在策略尾追加 `report-uri`，违规上报落 `data/logs/server.log`（WARNING，60s 同源节流） |

- **上线节奏**：新版先跑 report-only 观察（或按 `pnpm test:e2e:csp` 做隔离验证），在日志里按
  `grep "CSP violation" data/logs/server.log` 统计 `directive=... blocked=...`，确认无业务阻塞
  （图片/字体/WS/预览内嵌均已放开）后再切强制头（Django 侧改 `CSP_MODE` 即时生效；页面层改 nginx 头 + reload）；
- **策略要点**：`default-src 'self'`、`script-src 'self'`、`object-src 'none'`、`frame-ancestors 'self'`；
  按需放开 `worker-src blob:`（version-rocket 的 Blob 轮询 Worker）、`style-src 'unsafe-inline'`
  （Element Plus 注入内联样式）、`img-src data: blob:`、`frame-src blob:`（文档预览内嵌）、
  `connect-src ws: wss:`（WebSocket）——**connect-src 不含任何外部主机**（图标已离线化，见下）；
  `/media/`、`/api/static/`、`/api-docs/` 前缀豁免；
- **前端零在线依赖（离线/内网可用，2026-09-18）**：图标（菜单/路由 meta 的 `ep:*`、代码内的
  `ri/xxx`、图标选择器目录）全部来自**打包产物**——常用图标随包注册
  （`components/ReIcon/src/offlineIcon.ts`），其余按 set 前缀懒加载构建期内置的图标集
  （`src/components/ReIcon/src/iconRegistry.ts` 动态 import `@iconify/json`，产出同源 chunk，
  首屏不加载），**不再请求 api.iconify.design 等在线图标 API**；图标集 chunk 体积：
  ep ≈ 31 KB gzip / ri ≈ 262 KB gzip / fa-solid ≈ 212 KB gzip（按需加载）。
- **前端 SPA 的 CSP**（2026-09-18 已切强制）：SPA 由 nginx 托管时，Django 的响应头不覆盖 HTML 文档，
  需在 nginx 侧下发同一策略串——**三处同源**：`_CSP_DIRECTIVES`（服务端）、
  `xadmin-web/default.conf`（页面层）、`xadmin-client/scripts/csp-page-server.mjs`（隔离验证服务），
  漂移由 `tests/unit/common/test_csp.py::TestCSPPolicySync` 守护：

```nginx
# 页面层（当前生效）：强制头 + 上报到真实端点（/api/common/api/csp-report，注意 common 前缀，
# 写成 /api/csp-report 会 404 导致上报静默丢失）
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ws: wss:; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; report-uri /api/common/api/csp-report" always;
# 回滚：同一策略串改回 Content-Security-Policy-Report-Only 后 nginx -s reload
```

- **切换前的隔离验证**（替代「等真实流量观察零」，测试服不可达时尤其有用）：
  `pnpm build && pnpm test:e2e:csp`——以构建产物 + 强制头 + 真实浏览器扫核心页并断言零违规
  （`/__csp_probe` 负对照证明采集链路有效，同时断言 report-uri 可达 204）；
  跑批默认 `E2E_CSP_TLS=1`：验证服务以 **HTTPS** 提供（openssl 自签 + `ignoreHTTPSErrors`），
  **chromium 与 webkit 双浏览器**均验证零违规（WebKit 在 http 形态拒收 Secure Cookie 无法登录）；
- **注意（http 部署 + 认证 Cookie）**：生产构建的认证 Cookie 带 `Secure`（`src/utils/auth.ts` 的
  `import.meta.env.PROD` 分支），浏览器只在 **loopback（`localhost` / `127.0.0.1`）** 视为可信；
  用 IP 或域名走 http 访问时**所有浏览器都会拒收 Secure Cookie → 登录必然失败**
  （实测 2026-09-20：`http://192.168.0.200/` 登录后 cookie jar 为空、回跳登录页；同地址 https 正常。
  WebKit 在 loopback 下同样拒收，比 Chromium 更严格）；生产形态请按 §3 启用 HTTPS
  （`SECURITY_HTTPS_ENABLED: true`）。隔离验证的 webkit 覆盖已随 TLS 形态补上（2026-09-18），
  但线上 http 形态下 WebKit 用户仍无法登录——**生产/测试服部署应走 HTTPS**。

- 注意：`add_header` 在 nginx 中会**覆盖**继承的同名头，若已有 `X-Frame-Options` 等自定义头，
  请放在同一个 `add_header` 块内统一维护，避免互相覆盖。

