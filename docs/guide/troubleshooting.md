# 常见故障排查手册（二开 / 运维 FAQ）

> 定位：面向**二次开发者与运维使用者**的高频问答——每条 = 现象 → 原因 → 处置 → 相关文件 / 命令。
> 收录判据：**现象明显（报错、跳转、空白、502）但原因不直观**的问题。
>
> 与相邻文档的分工（这里不追求穷尽）：
>
> - 运维故障的完整清册（启动退出 / DB / Redis / Celery / 磁盘 / 恢复 / CVE）见 [ops/runbook.md](../ops/runbook.md)；
> - 「不报错、但功能静默失效」的开发坑见 [dev-pitfalls.md](../dev-pitfalls.md)；
> - 部署与配置速查见 [ops/deployment.md](../ops/deployment.md)；跨版本升级见 [upgrade-faq.md](upgrade-faq.md)；
> - 测试 / E2E 相关问题见 [xadmin-client/e2e/README.md](xadmin-client/e2e/README.md)。
>
> 通用前置三件套：`GET /api/common/api/health`（免认证，验 DB / Redis / Celery / 存储四项）、
> `data/logs/server.log`（按 `X-Request-Id` 检索单请求全链路）、`python manage.py doctor`
> （一条命令体检密钥 / DB / Redis / 语言包 / 权限点缺口 / 模块裁剪 / 前端契约，并给出修复命令）。

## 一、登录与认证

### 1. 登录接口返回成功，页面却跳回登录页 / 反复登录

- **现象**：`POST /api/identity/login/basic` 返回 1000，但前端立刻回到登录页、后续接口 401「未授权」。
- **原因**：生产构建的前端认证 Cookie 带 `Secure`（`xadmin-client/src/utils/auth.ts` 的 `import.meta.env.PROD` 分支），
  浏览器只在 **loopback（`localhost` / `127.0.0.1`）** 把 http 视为可信；用 **IP 或域名走 http** 访问时 Cookie 被丢弃，
  token 存不下 → 每个请求无凭据 → 后端视为匿名 → 回跳登录页（`crypto.subtle` 同因不可用，登录加密会回退 v1）。
- **处置**：改用 **HTTPS** 访问（生产/测试服的正确形态，`SECURITY_HTTPS_ENABLED: true`）；
  仅在确知不走 TLS 的内网环境，用 `VITE_COOKIE_SECURE=false pnpm build` 关闭 Secure（构建期注入，默认仍是 Secure）。
- **相关**：[ops/deployment-csp.md §8](../ops/deployment-csp.md)、[runbook.md §3](../ops/runbook.md)。

### 2. 登录报「临时 Token 校验失败」

- **现象**：登录接口返回 1001 / 400，文案「Temporary Token validation failed / 临时Token校验失败」。
- **原因**：`/api/identity/auth/token` 签发的临时 Token 与客户端指纹绑定（`User-Agent` / `Accept` 等请求头参与指纹）。
  取 Token 与提交登录**两个请求的特征不一致**（或复用了已被消费的旧 Token）即被判无效。
- **处置**：调试脚本里两步请求用**完全相同的请求头**；页面侧不要在同一页二次登录时复用旧 Token
  （产品侧已改为提交前统一刷新临时 Token）。
- **相关**：端点 `identity/urls.py`（`^auth/token$`）、`server/settings/libs.py` 的临时令牌限流配置。

### 3. 自动化脚本登录被「验证码 / 加密 / 密码密文」拦下

- **现象**：自动化登录一律失败，报「图片验证码校验失败」或 HTTP 500（明文密码被当密文解析）。
- **原因**：三个安全开关默认全开——`SECURITY_LOGIN_CAPTCHA_ENABLED`（图片验证码）、
  `SECURITY_LOGIN_ENCRYPTED_ENABLED`（登录请求体 AES 加密）、`SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED`
  （建号 / 改密链路按密文协议解析）。
- **处置**：自动化环境在 `config.yml` 置 `SECURITY_LOGIN_CAPTCHA_ENABLED: false`（脚本无验证码通道），
  请求体按要求走加密协议（账号与密码都用当次 Token 派生密钥加密）；测试档另可关密码密文开关。
  **不要为了测试把这些开关在生产关掉**。
- **相关**：`server/conf/settings_defaults.py`、[ops/config-reference.md §9.1](../ops/config-reference.md)。

### 4. 提示「当前服务器不允许登录」或频繁 429

- **原因**：登录接口限流（默认 `login: 50/h`，GET/POST 共享额度），共享出口（办公网 / 爬虫）易打满。
- **处置**：等待窗口重置；长期在 `config.yml` 的 `DEFAULT_THROTTLE_RATES` 调高 `login`，
  并让反向代理正确传递 `X-Forwarded-For`（配 `TRUSTED_PROXY_IPS`），避免全公司共用一个限流桶。
- **相关**：[ops/runbook.md §5](../ops/runbook.md)。

### 5. 夜间跑批时登录被 MFA 二次验证拦住

- **原因**：内置登录策略「非工作时间登录需二次验证」（`loadjson/loginaccesspolicy.json`）在 22:00–06:00 命中，
  登录返回 `mfa_required`；自动化环境读不到验证码（测试用 locmem 邮件后端）。
- **处置**：夜间跑自动化前，在「系统管理 → 登录策略」停用该条 `require_mfa` 策略（或用种子里的停用逻辑）；
  策略本身的行为由后端集成测试覆盖，不必靠真实时间点回归。
- **相关**：[architecture/mfa.md](../architecture/mfa.md)。

## 二、容器、配置与进程

### 6. 改了后端代码，接口行为没变

- **原因**：容器是**源码挂载 + 进程常驻**，默认不热加载；且 `celery-*` 是独立进程
  （导入 / 导出 / 报表等任务跑在 worker，只重启 web 不够）。`config.yml`、`XADMIN_APPS`、`MODULE_*` 等
  启动期配置变更也不被热加载覆盖。
- **处置**：`docker compose restart server celery-worker celery-heavy celery-beat`；
  开发期用 `bash ops/dev_up.sh --hot`（DEBUG=true → web 容器自动重载，celery / 配置变更仍需 restart）。
- **相关**：[dev-pitfalls.md](../dev-pitfalls.md) §6；[ops/dev_up.sh](../../ops/dev_up.sh)。

### 7. 中文界面变英文 / 新加的文案不翻译

- **原因**：`locale/*/LC_MESSAGES/django.mo` 是编译产物、不入库；`.po` 更新后未重编即回退英文。
  容器启动时 `entrypoint.sh` 会**按需自动编译**（幂等），但 bind mount 目录属主不符（无写权限）时只告警不阻断 → 仍为英文。
- **处置**：`python manage.py compilemessages`（升级后 `python manage.py post_upgrade` 已包含）；
  容器形态确认 locale 目录对容器用户（uid 1001）可写。
- **注意**：断言文案的测试要写 zh/en 双语，否则 CI（无 `.mo`）会挂。

### 8. 重建后端容器后，经反代访问 API 502

- **原因**：`server` 容器重建后 IP 变化，而反代若仍持有**旧的静态上游地址**，转发即失败。
- **处置**：先 `docker exec xadmin-nginx nginx -t` 校验配置；确认已是「运行期解析」（`resolver 127.0.0.11` + 变量 `proxy_pass`）
  时，server 就绪后 **10s 内自动恢复、无需重启**；仍是历史静态 upstream 形态才 `docker restart xadmin-nginx xadmin-web`。
- **相关**：[ops/runbook.md §17](../ops/runbook.md)、[ops/scale-out.md](../ops/scale-out.md)（多副本形态例外）。

### 9. 清库后登录 500 或配置不生效

- **原因**：旧配置仍缓存在 **Redis**（缓存 / 会话 / broker），清库只动了数据库。
- **处置**：按顺序操作——停应用容器（避免 DROP DATABASE 被会话占用）→ `DROP DATABASE` + `CREATE DATABASE`
  → **`redis-cli FLUSHALL`（关键，勿漏）** → 启动容器（entrypoint 自动 migrate）→ 初始化种子。
- **注意**：Redis 数据丢失只影响缓存 / broker；清空前先 `docker compose stop celery-*` 防任务丢失。
- **相关**：[ops/runbook.md §3](../ops/runbook.md)。

### 10. 服务启动即退出

- **常见三类**：无 `config.yml`（回落 `config_example.yml` 并自动生成密钥，显式配了却没填 `SECRET_KEY` 且 `DEBUG=false` 才拒绝启动）、
  `SECRET_KEY` 为空、端口被占用（exit code 10）。
- **处置**：`cp config_example.yml config.yml` 并填 `SECRET_KEY`（生产必须显式且多实例一致）；改 `HTTP_LISTEN_PORT` 或释放端口。
- **相关**：[ops/runbook.md §1](../ops/runbook.md)、[dev-pitfalls.md](../dev-pitfalls.md) §7。

### 11. 生产形态改了 `config.yml` 不生效

- **原因**：生产镜像**不烘焙 `config.yml`**，容器配置只认环境变量；而 compose 仅透传
  `DB_PASSWORD` / `REDIS_PASSWORD` / `DEBUG`。其余键（含 `SECRET_KEY` / `ALLOWED_HOSTS`）需在 overlay 的 `environment:`
  显式声明（列表 / 字典型用 JSON 串，如 `ALLOWED_HOSTS=["xadmin.example.com"]`）。
- **处置**：把需要的键加进生产 overlay 的 `environment:`（可配 `.env`），重建应用容器。
- **相关**：[ops/deployment-upgrade.md §6.1](../ops/deployment-upgrade.md)。

## 三、权限、菜单与元数据

### 12. 新增功能后菜单 / 按钮不显示（非超管 403）

- **原因**：权限码是 `动作:组件名`，**先入库再授权**才生效；新增端点（含自定义 `@action`）必须有对应权限点，
  否则非超管 `hasAuth(...)` 为假 → 页面 / 按钮直接不渲染。
- **处置**：`python manage.py sync_menu_permissions --dry-run` 查缺口 → 执行补齐；要固化进版本种子加 `--update-seed`；
  升级 / 新库重灌用 `python manage.py load_init_json`（幂等）。**改完必须重启进程**。
- **注意**：`PUT` 方法端点生成器不产出权限点，需**手工登记**（见 [recipes.md](recipes.md) R2）。
- **相关**：[dev-pitfalls.md](../dev-pitfalls.md) §2、[architecture/permission.md](../architecture/permission.md)。

### 13. 权限 / 菜单改了不生效

- **原因**：常规改动经 ORM / API 会触发缓存失效信号；**绕过信号的直改**（SQL 手工 UPDATE 关联数据）不会失效。
- **处置**：重新登录或等 TTL；立即生效执行 `python manage.py expire_caches system`（或重启 worker 触发清理）；
  持续不生效按 [architecture/cache.md](../architecture/cache.md) 的调试关键字核对缓存键。
- **相关**：[ops/runbook.md §9](../ops/runbook.md)。

### 14. 列表有数据但某列单元格全空

- **原因**：前端列完全由后端元数据生成——表格页 ViewSet 需同时混入搜索字段与列元数据动作；
  序列化器没把字段放进 `Meta.fields` / `Meta.table_fields` 就不下发。
- **处置**：补齐序列化器 `Meta.fields`（进接口）与 `Meta.table_fields`（进默认列）；
  字段权限树缺失跑 `python manage.py sync_model_field`；改完重启进程。
- **相关**：[dev-pitfalls.md](../dev-pitfalls.md) §1、[architecture/metadata-protocol.md](../architecture/metadata-protocol.md)。

## 四、模块裁剪

### 15. 某个模块停用后，接口 404 / 页面 404 / WebSocket 断开

- **现象**：`MODULE_DISABLE` 关掉一个模块后，前端仍留入口 → 点进去 404；或其 WS 通道连不上（4404）。
- **原因**：模块裁剪是**多层拦截**（模型加载 / 路由注入 / WebSocket 通道），被裁模块的路由与代码都不再注册。
- **处置**：裁剪后同步清理前端菜单入口；确认 `MODULE_PRESET` / `MODULE_ENABLE` / `MODULE_DISABLE` 与 `XADMIN_APPS`
  的取舍（`XADMIN_APPS` 管业务 app 的注册，`MODULE_*` 管框架能力模块），改完重启。
- **相关**：[architecture/模块化与功能裁剪.md](../architecture/模块化与功能裁剪.md)。

## 五、安全响应头（CSP）

### 16. 页面被 CSP 拦截 / 违规上报收不到

- **常见三类真实违规形态**（历史实测）：
  1. **内联脚本**：生产 `index.html` 的内联 `window.process` → 抽成同源 `<script src>`（`public/process-shim.js`）；
  2. **Worker blob**：version-rocket 用 `URL.createObjectURL(new Blob(...))` 建轮询 Worker → 策略加 `worker-src 'self' blob:`；
  3. **在线资源**：图标走在线 Iconify API → 前端图标已**离线化**（随包注册 + 按集懒加载），`connect-src` 不再放行外部主机。
- **上报端点**：`report-uri` 必须指向 `/api/common/api/csp-report`；写成 `/api/csp-report` 会 404、违规静默丢失。
- **相关**：[ops/deployment-csp.md §8](../ops/deployment-csp.md)、`packages/xadmin-common/common/api/csp.py`。

### 17. 切换强制 CSP 前怎么先验证

- **原因**：dev 链路无 CSP 头、Report-Only 只观测不拦截，「等真实流量观察零」在测试服不可达时无法累计。
- **处置**：`pnpm build && pnpm test:e2e:csp`——以构建产物 + 强制头 + 真实浏览器扫核心页，断言零违规，
  并含负对照探针（证明采集链路有效）与 report-uri 可达断言；验证服务默认 HTTPS（自签）以覆盖双浏览器。
- **相关**：[ops/release-checklist.md](../ops/release-checklist.md) §1、`xadmin-client/scripts/csp-page-server.mjs`。

## 六、任务与时间

### 18. 定时任务 / 报表执行时间不对

- **原因**：cron 表达式与任务调度按**服务端本地时区**解释（默认 `Asia/Shanghai`，配置 `TIME_ZONE`），不做用户级时区。
- **处置**：核对 `config.yml` 的 `TIME_ZONE` 与 beat 进程所在容器的时区；跨时区部署统一按服务端时区设定 `send_time`。
- **相关**：[ops/config-reference.md §9.1](../ops/config-reference.md)（`TIME_ZONE` 默认值）。

## 七、二次开发高频

### 19. 接口返回 200，但页面没有任何提示（业务错误被吞）

- **现象**：请求 HTTP 200，响应体 `code` 非 1000（如 1001），页面既不报错也无提示。
- **原因**：http 拦截器只处理 **HTTP 层**错误（4xx / 5xx / 网络），**200 + 业务码必须页面显式处理**——
  `.then(res => { if (res.code === SUCCESS_CODE) 成功提示 })` 只判成功，失败分支被静默丢弃。
- **处置**：页面显式处理失败分支（`ElMessage.error(res.detail || 兜底文案)`），并独立成条语句、成功分支 `return`；
  新写页面不要假设业务错误会被全局拦截。
- **相关**：前端 http 封装与页面 hook。

### 20. 本地前后端联调：接口 404 / 请求没打到后端

- **现象**：vite dev 下调接口 404 或连接失败。
- **原因**：dev server 的 `/api` 代理目标端口与后端实际端口不一致；或端口被别的进程占用
  （Docker 后端 8896 / E2E 后端 18896 / 桩 LLM 18897）。
- **处置**：确认 vite 代理指向后端（默认 8848 → 8896）；跑 E2E 用隔离端口，勿与 Docker 的 8896 混用。
- **相关**：[xadmin-client/e2e/README.md](xadmin-client/e2e/README.md)。

### 21. 上传图片 / 大文件失败（1002 / 1003）

- **原因**：`code=1002` 类型不在白名单、`1003` 超过大小上限（站点配置 `PICTURE_UPLOAD_SIZE` / `FILE_UPLOAD_SIZE`）。
- **处置**：按需放开白名单 / 上限，并**同步调大反向代理的 `client_max_body_size`**（否则请求在到达应用前就被拒）。
- **相关**：[ops/runbook.md §14](../ops/runbook.md)。

### 22. 菜单点进去空白 / 渲染成别的组件，且控制台无报错

- **原因**：前端动态路由按菜单的 `component`（组件目录）解析组件文件；解析器若按「子串包含取首个命中」，
  页面目录下**新增的任意文件**可能被优先命中 → 整页渲染成该子组件（无报错、无白屏，表现为页签 / 表单全消失）。
- **处置**：确认菜单 `component` 与前端组件文件路径一致（命名基本口径与例外清单见
  [guide/menu-maintenance.md](menu-maintenance.md)）；定位时用 DOM 的 `data-insp-path` 确认实际挂载的组件文件再改。

## 八、诊断速查

| 目标 | 命令 / 入口 |
|------|-------------|
| 环境体检（密钥 / DB / Redis / 语言包 / 权限点 / 模块 / 契约） | `python manage.py doctor` |
| 健康检查四项 | `GET /api/common/api/health`（免认证） |
| 单请求全链路日志 | `data/logs/server.log`，按 `X-Request-Id` 检索 |
| 权限点缺口 | `python manage.py sync_menu_permissions --dry-run` |
| 配置缓存失效 | `python manage.py expire_caches config_*` |
| 语言包编译 | `python manage.py compilemessages` |
| 升级后补全（种子 + 语言包 + 缓存 + 权限扫描） | `python manage.py post_upgrade` |
| 重启后端进程 | `docker compose restart server celery-worker celery-heavy celery-beat` |
| Celery 在线检查 | `bash ops/check_celery.sh [celery\|heavy]` |

> 上面任一命令的输出都带「下一步修复命令」；失败项返回非零退出码，可直接接进巡检。
