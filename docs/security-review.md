# 安全自查清单（归档）

> 背景：半年规划 P5/T5.3「安全自查二期」。本文档归档每轮安全自查的范围、结论与遗留项，
> 后续自查在本文追加新章节，不另立文档。
> 关联：ADR-001（CSRF/JWT-only 决策）、docs/exception-handling.md（错误脱敏）、
> docs/architecture/permission.md（权限体系）。

## 二期自查（2026-09-06）

范围：Flower 认证收尾、X-Frame-Options、Referer 校验、上传类型校验复核。
越权矩阵测试（水平/垂直越权用例集入 CI）的开发侧完成于同日（见下节），实跑验证留待测试窗口。

### 1. Flower 任务监控认证 ✅ 本次收尾

| 项      | 内容                                                                                                                                                                                                                                                                           |
|--------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 风险     | 历史版本 `CELERY_FLOWER_AUTH` 默认值硬编码弱口令（`flower:flower123.` / `flower:flower` 双兜底），部署方不改即带弱口令暴露监控面板                                                                                                                                                                              |
| 处置     | ① `server/conf/` 默认值改为空串；② `common/management/commands/services/hands.py` 移除 `or 'flower:flower'` 兜底；③ `common/management/commands/services/services/flower.py` 启动守卫：未配置认证时仅允许绑定 `127.0.0.1`/`localhost`，绑定其他地址直接 `sys.exit(11)` 拒绝启动，未配置认证时不再向 flower 传空的 `--basic-auth=` 参数；④ `config_example.yml` 补充配置示例与说明 |
| 验收     | 全仓 grep 无 `flower123`/`flower:flower` 硬编码残留；生产部署必须显式配置 `CELERY_FLOWER_AUTH` 才能对外暴露监控面板                                                                                                                                                                                       |
| 面板访问链路 | 管理台经 `common/celery/flower.py` 代理访问，代理侧自动携带所配置的 basic-auth，前端无需感知                                                                                                                                                                                                            |

### 2. X-Frame-Options ✅ 无需变更

- `XFrameOptionsMiddleware` 已启用（`server/settings/base.py` 中间件链），未显式设置 `X_FRAME_OPTIONS`，取 Django 默认
  `SAMEORIGIN`，管理台页面不可被第三方 iframe 嵌套。
- 既有豁免均为有意保留：`common/swagger/views.py`（API 文档页）、`common/celery/flower.py`（Flower 代理页）需要以 iframe
  内嵌进管理台，属功能必需。
- 结论：默认防线有效，豁免面最小化，记录即可。

### 3. Referer 校验 ✅ 默认关闭属合理决策

- `REFERER_CHECK_ENABLED`（`server/conf/` settings 段，实现在 `server/middleware.py`）默认 `False`。
- JWT-only 架构（ADR-001）下 API 不依赖 Cookie 凭证，CSRF/Referer 伪造面远小于 Cookie 会话架构；开启开关可作为纵深防御选项。
- 结论：维持默认关闭；面向纯浏览器 Cookie 场景的部署可在 config.yml 打开。

### 4. 上传类型校验 ✅ 白名单已有，记录一个低风险项

- `common/core/modelset/upload.py`：扩展名白名单 `FILE_UPLOAD_TYPE = ["png", "jpeg", "jpg", "gif"]` + 大小上限（
  `FILE_UPLOAD_SIZE`，可按站点配置 `PICTURE_UPLOAD_SIZE`），不符合即拒绝（code=1002/1003）。
- 低风险记录：未校验文件 magic bytes / 实际内容类型，理论上可在白名单扩展名内伪装内容。上传目录非可执行目录、Django
  静态服务不解析脚本，可利用面很小。
- 处置：不阻塞发版；后续如引入更广泛文件类型（办公文档/压缩包）上传，必须同步引入 python-magic 内容校验。

### 5. 既有基线复核确认（未发现回退）

- SECRET_KEY 生产拒启校验、ALLOWED_HOSTS/CORS 配置化（conf.py 默认收紧，`config_example.yml` 有注释示例）。
- JWT 双 Token 轮换 + 黑名单、六类接口限流（login 50/h 等）、登录锁定与异地登录检测（`SECURITY_*` 配置段）。
- compose 无 privileged、PG/Redis 密码经 `.env` 注入。

## 越权矩阵用例集（2026-09-06，T5.3 开发侧交付）

`tests/integration/system/test_privilege_escalation_matrix.py`：水平/垂直越权 HTTP 集成测试 19 例
（矩阵编号 M01-M17，随 pytest 全量进 CI，`test.yml` 无需改动）。

设计要点：

- **载体选择**：数据权限以 demo.Book（owner 字段 `admin`）为载体，垂直越权以 system.user 管理端接口为目标；
  菜单授权遵循生产种子惯例（列表路由 `$` 精确锚定、详情路由 `(?P<pk>[^/.]+)$` 正则，见 `loadjson/menu.json`）；
- **矩阵分层**：认证边界（匿名 401 / 伪造 JWT 401+40001）→ 接口权限（无菜单 403、方法越权 403、
  列表授权不隐含详情授权、白名单路由方法面、角色/菜单停用即时吊销）→ 自提权（关联字段 roles 经数据
  权限过滤，无法给自己授予不可见角色，400 且零副作用）→ 数据权限（列表水平隔离、读/改/删他人数据一律
  400 且零副作用、未授权默认拒绝、菜单作用域授权不跨菜单泄漏）→ 字段权限（白名单同时约束读侧响应裁剪
  与写侧字段忽略）；
- **缓存纪律**：权限结果按用户+方法缓存（MagicCacheData 24h），每条用例在首个请求前完成全部授权布置，
  用例间由 conftest `_clean_cache` 隔离；
- **验收口径**：T5.3 规划要求「越权用例 ≥10 条入 CI」，实际交付 19 条；断言同时覆盖 HTTP 状态、业务码与
  数据库零副作用（被拒操作不留痕）。

## 依赖升级窗口一期（2026-09-06，P5/T5.1 前置执行）

pip-audit（2.10.1，OSV/PyPI 数据库）对生产依赖实测：**45 个已知漏洞 / 5 个包**，全部有修复版本，已一次性清零：

| 包                   | 升级                  | 修复的漏洞                                                                  |
|---------------------|---------------------|------------------------------------------------------------------------|
| django              | 5.2.9 → **5.2.17**  | PYSEC-2026-42~55/197~201/2090~2092/2448~2449/3717 等 20+ 项（LTS 补丁系列内升级） |
| djangorestframework | 3.16.1 → **3.17.2** | CVE-2026-73228 / CVE-2026-73229                                        |
| daphne              | 4.2.1 → **4.2.3**   | PYSEC-2026-213 / PYSEC-2026-214                                        |
| pyzipper            | 0.3.6 → **0.4.0**   | PYSEC-2026-3044                                                        |
| requests            | 2.32.5 → **2.33.0** | PYSEC-2026-2275                                                        |
| openpyxl            | 3.2.0b1 → **3.1.5** | TD-18 收尾：PyPI 上 3.2 系列仅有 beta，回退至最新稳定版                                 |

复测：`pip-audit` **0 漏洞**；`manage.py check` 无告警；`manage.py spectacular` 正常导出（200 paths）。未跑
pytest（本轮按「忽略测试」约定），合入前 CI 门禁兜底。

client 侧（pnpm audit 11.25，2026-09-06 实测）：**40 漏洞（20 high / 16 moderate / 4 low），全部位于传递依赖**（构建链
rollup/esbuild/postcss/picomatch/nanoid 等 + vue3-ts-jsoneditor 引入的
devalue/svelte/preact/fast-uri/form-data/lodash-es）。直接依赖整体较新（落后多为 dev 工具 minor），未动 lockfile——修复需升级
`vue3-ts-jsoneditor` 3.3→3.4.1 并刷新构建链，留待升级窗口在独立分支 + 全量门禁验证。另登记：`crypto-js` 已被上游标记
Deprecated，窗口期评估替换（WebCrypto 原生 API 或 aes-js）。

## 遗留项

| 项                              | 状态           | 说明                                                              |
|--------------------------------|--------------|-----------------------------------------------------------------|
| 越权矩阵测试（水平/垂直越权用例 ≥10 条入 CI）    | ✅ 已完成        | 2026-09-06 交付 19 例（M01-M17）入 `tests/integration/system/test_privilege_escalation_matrix.py`，已常态化入 CI 运行；2026-09 扩容至 M18-M29 |
| 上传 magic bytes 校验              | ✅ 已实现        | `common/core/modelset/upload.py` 的 `FILE_UPLOAD_MAGIC` 文件头校验已上线（扩展名白名单 + 魔数双重校验） |
| client pnpm audit 高危清零（40 → 0） | ✅ 已清零        | 2026-09-11 四期复核：官方源 `pnpm audit --audit-level high` **0 漏洞**（vue3-ts-jsoneditor 3.4.1 + 构建链刷新） |
| server pip-audit               | ✅ 已清零        | 2026-09-06，见上节                                                  |

## 三期自查（2026-09-11）：JWT 专项审计（N5）

范围：签发/校验/吊销全链路（`common/core/auth.py`、`identity/views/auth/`、
`server/settings/libs.py` SIMPLE_JWT 配置、client 侧 token 消费）。

### 审计结论：11 项达标，3 项已知边界（无需改动，记录触发条件）

**达标项**

| # | 项 | 现状 |
|---|----|------|
| 1 | token 类型限制 | `AUTH_TOKEN_CLASSES` 仅 `ServerAccessToken`，refresh token 不能当 access 用 |
| 2 | 生命周期 | access 1h / refresh 15d，均 config.yml 可配（`conf.py` libs 默认） |
| 3 | 轮换与黑名单 | `ROTATE_REFRESH_TOKENS` + `BLACKLIST_AFTER_ROTATION` 开启，DB 级 OutstandingToken/BlacklistedToken |
| 4 | 登出吊销 | access 进 `BlackAccessTokenCache`（md5，按 exp 设 TTL）+ refresh 进 blacklist；MFA 二次确认状态同步清除 |
| 5 | 强制下线（用户级） | `UserTokenRevokedCache` 失效时间戳 + access `iat` 比较，被踢时刻前签发的 token 全拒（`test_force_logout.py`） |
| 6 | 强制下线（会话级） | token 内嵌 `sid` claim（refresh 派生自动继承），`SessionTokenRevokedCache` 按 sid 精确拒绝（`test_user_session.py`） |
| 7 | 暴破防护 | 登录/验证码登录 `LoginThrottle` 50/h；refresh 端点走全局 `AnonRateThrottle` 60/m；PAT 有凭证级 `PAT_RATE_LIMIT` |
| 8 | 签发密钥 | SECRET_KEY 生产拒启校验（base.py），密钥为空无法伪造 token |
| 9 | 算法混淆 | 算法固定 HS256（simplejwt 按 settings 白名单，不接受 header 算法参数） |
| 10 | PAT 平行通道 | sha256 存储、IP 白名单 fail-closed、scope 精确审计、吊销即时生效（ADR-008） |
| 11 | 前端加密纵深 | AES v2 协议（ADR-011）落地，凭证传输双格式过渡 |

**已知边界（记录触发条件，暂不改动）**

| # | 边界 | 评估 | 触发条件 |
|---|------|------|----------|
| B1 | `GetUserFromAccessToken`（token_type=refresh 的 AccessToken 子类）被 API 日志中间件用于「请求体携带 refresh token 时反查用户归属」 | 仅影响审计日志归属，不参与任何授权决策；解析失败静默忽略；refresh token 本身是签名凭证，伪造无收益 | 若未来日志归属被用于计费/追责等强场景，需先 `verify()` 再归属 |
| B2 | Cookie 认证（`X-Token` cookie → Bearer）服务于 Flower 代理等 django-proxy 页面 | 与 localStorage token 同级 XSS 暴露面（cookie 非 HttpOnly，由前端写入）；Proxy 页面为既有功能决策 | 若引入不受信任的第三方页面嵌入，需改 HttpOnly + CSRF 双提交 |
| B3 | HS256 + `SIGNING_KEY=SECRET_KEY` 复用 | 单服务部署无密钥分发问题；轮换 = 轮换 SECRET_KEY（登出全体用户，可接受） | 服务拆分/多实例异密钥需求出现时，评估 RS256/JWK（simplejwt 原生支持） |

## 四期登记（2026-09-11）：SCIM 目录同步 / 备份告警 / CSP

### S1 SCIM 2.0 用户目录同步（`/api/scim/v2`）

- **凭证分离**：独立 Bearer Token（`SCIM_TOKEN`，`secrets.compare_digest` 比较），
  与 JWT/PAT 完全不同链路——业务身份（JWT）访问 SCIM 一律 401/403，SCIM 凭证也不进业务授权；
- **默认休眠**：`SCIM_ENABLED=false` 默认关闭（未开启时任何请求 403），令牌未配置时 401；
  两个配置项均 `access=false`，不对外回传；
- **写入面收口**：仅 Users/Groups 的 CRUD + PATCH 子集，未实现 bulk/sort/etag/changePassword
  （ServiceProviderConfig 显式声明 false，不静默忽略）；
- **凭据不下发**：payload 无 `password` 时置不可用密码（登录走既有 OAuth2/OIDC 联邦）；
- **停用即失效**：`active=false` / DELETE 复用 `force_logout_user`（令牌失效时间戳 + refresh
  拉黑 + WS 踢线 + UserSession 置离线），与在线用户强制下线同一套链路；
- **审计**：所有写操作落 `OperationLog`（`auth_type=scim`），记字段级 changes，
  **刻意不记请求体**（可能含 password）；令牌本身不落日志；
- **限流**：`ScimThrottle` 凭证级（`SCIM_RATE_LIMIT`，默认 600/min），凭证泄漏时爆炸半径可控。

测试：`tests/integration/system/test_scim_api.py`（11 例：休眠 403 / 缺失与错误令牌 401 / 业务身份不可访问 /
能力声明 / 凭证级限流 / Users CRUD 与 `userName eq` 过滤 / 重名 409 / 不支持的 filter 400 invalidFilter /
停用触发会话失效 / DELETE 语义为停用 / 写操作审计（且不落请求体）/ Group 生命周期与成员增删）。

### S2 备份失败告警（`POST /api/common/api/backup-alert`）

- 独立令牌 `X-Backup-Token`（`BACKUP_ALERT_TOKEN`，默认空 = 端点恒 403），
  比较用 `secrets.compare_digest`；令牌不对只返回通用 403，不泄露配置状态；
- 60s 同源节流（防脚本循环重试刷告警）；订阅缺失/收件人为空时自愈补建（存量库 post_migrate
  早于本消息注册时不会静默丢失告警）；
- 告警投递失败不影响备份主流程（脚本侧只追加 WARN，退出码仍反映备份失败）；
- 实测：本机 PG 演练库真实失败路径触发告警 + 不可达地址降级（见 `docs/ops/backup-drill-2026-Q4.md`）。

### S3 CSP（django-csp 4.0 + 运行期模式开关）

- **默认 report-only（观察期）**：不拦截请求，违规经 `report-uri` 上报到
  `/api/common/api/csp-report`（只记 WARNING 日志 + 60s 节流，不落库）；
- 模式与上报地址运行期可配（`CSP_MODE`：disabled/report-only/enforce；
  `CSP_REPORT_URI`），观察一周后切 enforce 无需重新发版；
- 策略：`default-src 'self'`、`object-src 'none'`、`base-uri 'self'`、`frame-ancestors 'self'`；
  仅按需放开 `style-src 'unsafe-inline'`（Element Plus 注入内联样式）、`img-src data: blob:`、
  `frame-src blob:`（文档预览内嵌）、`connect-src ws: wss:`（WebSocket）；
- `/media/`、`/api/static/`、`/api-docs/` 前缀豁免（大文件/静态资源不背策略头）；
- 已知边界：前端 SPA 由 nginx 托管时，其自身的 CSP 需在 nginx 侧下发同一策略串
  （见 `docs/ops/deployment.md`）；本处 django-csp 覆盖 Django 渲染页与 API 响应。
  **2026-09-16 已落地**：`xadmin-web/default.conf` 的 `location /` 已下发同策略串
  （`Content-Security-Policy-Report-Only`，report-uri 同为 `/api/csp-report`）；
  与 Django 侧同步切强制头（去掉 `Report-Only`）；**策略串变更需两处同步**
  （Django：`server/settings/base.py` `_CSP_DIRECTIVES`；nginx：`xadmin-web/default.conf`）。

测试：`tests/unit/common/test_csp.py`（8 例：默认观察头/切 enforce/disabled/report-uri 注入/
静态前缀豁免/上报落日志与节流/CSP3 信封与非法 JSON 容错）。

### 依赖窗口四期复核（2026-09-11）

| 项 | 结果 |
|---|---|
| server `pip-audit`（已安装环境，等价 CI `-r requirements.txt` 口径） | **0 漏洞** |
| client `pnpm audit --audit-level high`（官方源，与 CI 同口径） | **0 漏洞** |
| renovate PR 清理 | 无待处理 renovate PR（server 仅 1 个人工 PR #104，client 0 个） |
| openpyxl | 3.1.5 = PyPI 最新，无窗口 |
| element-plus | 当前版本即最新，无窗口 |
| vite | 8.2.2 → 8.3.0（minor）可用，走 renovate 常规窗口升级，不在本次手工变更 |
| typescript | 6.0.3 → 7.0.2 已评估：**暂不升级**（vue-tsc 与 TS 7 不兼容），见 ADR-014 |
| 其他窗口 | dev 工具 minor（@iconify/json、@types/node、lint-staged、pinyin-pro）与
  major（@iconify/vue 4→5、cropperjs 1→2、cssnano 8→9、postcss-import 16→17）交由 renovate 常规窗口 |

## 五期登记（2026-09-27）：架构盘点安全项处置与接受项台账

来源：`docs/plans/全面架构盘点与重构方案-2026.09.md` §3.2（S-1~S-6）与 §十执行记录。

### S-1 接口文档登录：开放重定向 + 锁定旁路 —— 已处置

- next 回跳同源校验（`common/swagger/views.py::_safe_next_url`，`url_has_allowed_host_and_scheme`，
  `ALLOWED_HOSTS` 的通配 `*` 不并入判定）；外部域一律回落默认文档地址。
- 接入主登录同源锁定：失败累计 / 成功清零共用 `LoginBlockUtil` / `LoginIpBlockUtil` 计数键，
  锁定语义与主链路一致。
- 单列更严限流 `api_docs_login`（10/m），与匿名默认限流（60/m）叠加。

### S-2 出站请求 SSRF（Webhook / AI base_url） —— 已处置

- 统一守卫 `common/utils/outbound.py`：协议白名单；link-local（含云元数据）/ 多播 / 保留 /
  unspecified / 6to4 / Teredo / IPv4-mapped 地址**任何模式拒绝**；私网与环回按场景放行。
- Webhook：写入侧校验（https 强制 + loopback http 联调例外 + IP 字面量归属），
  发送侧严格解析 + **固定解析结果连接**（`pinned_request`：IP 直连 + Host 头 + TLS SNI 域名），
  消除 DNS rebinding 窗口；`OUTBOUND_ALLOWED_HOSTS`（SysConfig）是私网目标的唯一放行途径。
- AI base_url：允许私网 / 环回（内网自建推理与本地联调），拒绝元数据与链路本地地址；
  写入侧不做域名解析（避免本地 DNS 屏蔽误伤），域名指向的元数据由执行侧语义收敛（响应非 LLM 格式）。

### S-3 `/media/` 直链无鉴权 —— 已处置（X-Accel 内转方案）

- nginx `/media/` 静态直出移除，统一转发应用鉴权（Cookie JWT / session，匿名 403）；
  鉴权通过后经 `X-Accel-Redirect` 内转到 `internal` 的 `/_protected_media/`（不可外部寻址）由
  nginx 零拷贝直出；`MEDIA_X_ACCEL_PREFIX` 默认空（应用进程输出，DEBUG 恒直出）。
- 口径：本视图鉴权粒度 = 登录态；细粒度文件授权与访问审计仍由受鉴权 download / preview 端点承担。
- 守护：`tests/integration/common/test_media_access.py`（含 nginx 配置同源断言）。

### S-4 消息模板预览 XSS 面 —— 已处置

- `MessageTemplateForm.vue` 的默认正文与预览结果统一经 `sanitizeHtml`（DOMPurify，
  与公告展示同源白名单），存储型 XSS 面收敛。

### S-5 用户枚举 —— 已处置

- 重置 / 登录验证码发送链路不再区分账号是否存在：目标不存在（或停用）静默成功、
  不发送验证码、响应文案与结构一致（时间开销对齐：等价模板渲染）。
- 注册链路保留「已存在」明确文案：注册必须告知占用，否则用户无法完成注册（接受项，见表）。

### S-6 接受项台账（接受理由 + 重开条件）

| 项 | 现状 | 接受理由 | 重开条件 |
|---|---|---|---|
| JWT 存 JS 可读 Cookie（`xadmin-client/src/utils/auth.ts`） | `SameSite=Lax` 挡 CSRF；XSS 面由 CSP 强制头（`script-src 'self'`）兜底 | 无感刷新与既有前端链路依赖 Cookie 读 token；改为 HttpOnly 需要整套刷新链路改造 | 出现可绕过 CSP 的 XSS 面，或前端改造为 BFF 形态 |
| HTTP 直连部署时 Cookie 无 Secure（`SECURITY_HTTPS_ENABLED` 三态） | 非 HTTPS 部署不下发 Secure（否则浏览器丢弃，登录循环）；生产推荐 HTTPS + HSTS（nginx 已备注释开关） | 保留内网 HTTP 直连可用性 | 全站强制 HTTPS 后，服务端置 `SECURITY_HTTPS_ENABLED=true` 并启用 HSTS |
| 注册链路账号占用提示（S-5 保留项） | 注册时明确返回「用户名/邮箱/手机已存在」 | 注册流程必须告知占用；已有验证码 + 限流门槛 | 注册改为邀请制（无需公开提示占用） |
| Django 6.0.8 停留（6.1 发布后该线退出安全支持） | 生产运行 6.0.8；升级 6.1 的唯一阻断项 `django-celery-beat 2.9.0` 声明 `Django<6.1`（2026-09-29 实查 PyPI：最新版仍为 2026-02-28 的 2.9.0，无新版时间表） | beat 承载定时任务调度（beat/results 全家桶），属不可替换核心依赖；ADR-004（2026-09-16）实测 6.1.1 全量零回归，但按「声明矩阵未覆盖即维持」纪律整体回滚；停留期以**安全公告监控 + 必要时补丁后移**兜住风险（见六期登记） | `django-celery-beat` 发布声明支持 `Django>=6.1`（beat/results/timezone-field 三件同步），或 6.2 LTS 发布且全家系声明覆盖；或出现影响本部署形态的 6.0 线高危公告（此时走 override 升级流程：全量门禁 + 回滚预案） |

## 六期登记（2026-09-29）：依赖线 EOL 停留与 Python 口径对齐

本批伴随 P0 安全收口交付（越权面收口 / 审计只读化 / 并发正确性），依赖线两项当日复核：

### S-1 Django 6.0.8 EOL 期安全监控 —— 已登记

**事实**：Django 6.0 系列自 6.1 发布（2026-08-05）起不再获得安全修复；升级 6.1 的阻断面经实查（PyPI 元数据 + 本地安装声明）收敛为**唯一一个包**——`django-celery-beat 2.9.0` 声明 `Django<6.1,>=2.2`；其余全部放行（`django-timezone-field 7.2.2` 声明 `>=4.2,<6.2`，卡的是未来的 6.2 LTS，不阻断 6.1）。

**决策**：维持 6.0.8，不启动 override 升级（依据 ADR-004「声明矩阵未覆盖即维持」纪律与用户确认）。

**监控动作（每次发布窗口 + 每季度依赖窗口执行）**：

1. 核对 Django 官方安全发布公告（weblog security releases），确认是否有落在 **6.0 线**且影响本部署形态（Django + DRF + channels/daphne + celery 栈，无第三方 Django 插件面）的漏洞；
2. 命中时两条处置路径，按影响面选择：
   - **紧急升级**：走 6.1 override 流程（`[tool.uv] override-dependencies` 注释原因与撤销条件 → 全量门禁 + beat 周期任务链路专项验证 + 回滚预案），升级后同步更新 ADR-004；
   - **补丁后移**：上游修复落到 6.0 线的补丁后移至自有镜像，记录补丁来源（commit/PR）、适用版本与到期时间（下一次升级窗口必须重新评估）；
3. 复核结论追加到 [ops/release-checklist.md](ops/release-checklist.md) 执行记录（与季度依赖窗口同窗口记录）。

**重开条件**：`django-celery-beat` 声明放宽到 `Django>=6.1`（含 beat/results 全链路），或 6.2 LTS 发布且全家桶声明覆盖——届时按 ADR-004 升级流程（独立分支 + 兼容矩阵 + 全量门禁）执行。

### S-2 Python 口径对齐 3.14 —— 已执行

漂移事实：容器基线（`python:3.14.7-slim`，prod/base/dev）与本机 venv 早已是 3.14.7，但 `pyproject.toml` 的 `requires-python`/mypy `python_version` 与 9 处 CI workflow 仍停 3.13。
处置：口径统一到 3.14（`requires-python = ">=3.14"` + mypy 3.14 + CI 9 处 + `uv.lock` 重生成），server 内 6 处文档与文档站 4 文件 5 处受保护事实（`check_doc_facts.py` 的 `workflow:python` 源）同步。

## 七期登记（2026-10-02）：敏感读取审计/运行日志脱敏收口 + CSP 分层口径 + 供应链停维核实

本批伴随 O8-5～O8-8 安全收口交付（操作日志敏感 GET 埋点 / 运行日志脱敏过滤器 / 导出导入专用限流，见 `NEXT-DEV-PLAN.md` 执行记录九），登记两项评估结论与一份供应链核实台账。

### S-1 CSP：Django 侧默认 report-only 与 nginx 页面层强制头分层 —— 已评估，维持现状（O8-7）

**事实**：nginx 页面层（`xadmin-web/default.conf`）对页面流量下发**强制** `Content-Security-Policy`（2026-09-16 服务端 CSP 切换同口径）；Django 侧 django-csp 生成策略后由 `CSPModeMiddleware` 按 `CSP_MODE` 运行期档位下发，默认 `report-only`（观察期）。直连 Django 不经 nginx 的流量（仅 JSON API 与 swagger/api-docs）因此只在观察档。

**评估结论（维持现状，属有意分层）**：

1. 浏览器可达的 HTML 面（前端页面）全部经 nginx，已强制——CSP 实际防护的脚本执行面没有缺口；
2. JSON API 响应无脚本执行面，强制 CSP 无防护增量；
3. Django 直出 HTML 仅 swagger/api-docs：django-csp 策略按 swagger 资源放行（S3 落地收口），切 enforce 是行为变更（可能打断 swagger 内联脚本面），需独立观察窗口，不由默认值翻转完成；
4. 需要对 API 面强制的部署，运行期把系统配置 `CSP_MODE=enforce` 即可，无需改代码。

**重开条件**：django-csp 策略对 swagger 的 enforce 兼容性完成一轮观察验证（CI 含 api-docs 页面用例全绿），或出现「绕过 nginx 直连 Django 的 HTML 面」的新部署形态。

### S-2 供应链：疑似停维依赖核实台账（O9-6，季度依赖窗口执行）

`pyproject.toml` 六个「疑似停维」直连依赖 2026-10-02 经 PyPI 元数据逐包核实（发布时间取 PyPI 上该版本 `upload_time`），结论与重开（处置）条件如下：

| 包 | 锁定版本 | 最近发布 | 用途（代码位置） | 核实结论 | 处置 / 重开条件 |
|---|---|---|---|---|---|
| `unicodecsv` | 0.14.1 | **2015-09-22** | CSV 导入解析/导出渲染（`common/drf/parsers/csv.py`、`common/drf/renders/csv.py`） | **停维**（11 年无发布，作者已弃） | 出现 CVE 或需 Python 3.15 兼容时替换为 stdlib `csv`（手工包 encoding，改动面 2 文件）；无 CVE 前不动 |
| `django-ranged-response` | 0.2.0 | **2017-07-18** | 验证码图片 Range 响应（`captcha/views.py`） | **停维**（9 年无发布） | 跟随 `django-simple-captcha` 生态决策；出现 CVE 时用 Django 原生 `FileResponse` Range 支持替换（改动面 1 文件） |
| `user-agents` | 2.2.0 | **2020-08-23** | UA 解析（操作日志 system/browser 列，`common/utils/request.py`） | **停维**（6 年无发布；底层 ua-parser 亦低频） | UA 解析仅做日志展示非安全判定；出现解析错乱面扩大或 CVE 时评估换 `ua-parser` 直连/自维护精简正则 |
| `ldap3` | 2.9.1 | **2021-07-18** | LDAP 登录/同步客户端（`identity/ldap/client.py`） | **事实停维**（5 年无稳定版；2.10.2 停在 rc；无官方公告，上游 issue 1169 证实停滞） | LDAP 功能默认关闭（F7-3）；启用部署出现 CVE 时补丁后移（六期 S-1 同款流程）或换 `python-ldap`/社区 fork，走独立立项 |
| `pilkit` | 3.0 | 2023-09-27 | 图片处理器（缩略图 ResizeToFill，`common/fields/image.py`、`identity/models/user.py`） | **低频维护**（3 年无发布，非弃维信号明确） | 随 PIL 生态观察；Pillow 大版本升级门禁若报 pilkit 不兼容，届时评估 |
| `pyexcel` | 0.7.6 | **2026-06-29** | xlsx 解析（`common/drf/parsers/excel.py`） | **仍活跃**（本轮核实纠正了此前「疑似停维」判定，从清单移除） | 无动作；`pyexcel-xlsx 0.6.1` 适配器较旧，随季度窗口观察 |

**窗口纪律**：本表每季度依赖窗口（与六期 S-1 监控动作同窗口）复核一次「最近发布」列与各包 CVE 公告，结论追加到 [ops/release-checklist.md](ops/release-checklist.md) 执行记录。

### S-3 前端供应链：wangEditor 停维登记与 @iconify/vue 锁版口径（O9-7 / O9-8）

**wangEditor（`@wangeditor/editor` 5.1.23，`xadmin-client`）—— 停维已登记**

事实：上游 `wangeditor-team/wangEditor` 于 2023-08 发布官方公告「暂停维护（作者时间原因）但仍可继续使用」（issue #5678，issue 创建随之受限），此后无版本发布；npm 包仍可安装使用，无修复通道。

仓内使用面：富文本编辑与回显（`src/components/RePlusPage/src/components/WangEditor.vue`、消息模板正文 `MessageTemplateForm.vue`、公告 `useNoticeFormOptions.tsx` / `NoticeShow.vue`，引导见 `src/utils/wangEditorBoot.ts`）。

**触发条件（命中任一即立项替换评估，候选 Tiptap / Lexical 等同能力编辑器）**：

1. 出现 XSS 或其他安全公告且上游无修复（包停维 = 不会出现官方补丁），此时先做净化层加固（模板链路已有净化补链）再评估替换；
2. 出现协同编辑 / 更复杂排版等 wangEditor 无 roadmap 的产品需求；
3. Vue 主版本演进导致其 Vue 适配层（`@wangeditor/editor-for-vue`）不可用。

替换前不动现有集成（组件化收口在 `WangEditor.vue` 单文件，替换面可控）。

**@iconify/vue（`5.0.1` 精确锁版，`xadmin-client/package.json`）—— 评估结论：维持，无需放开**

「唯一精确锁版项」并非漂移：renovate 配置 `rangeStrategy: "pin"`（`xadmin-client/renovate.json`）的既定终态就是精确锁版，该版本号即 renovate 更新时写回的形态；renovate 对精确锁版依赖照常自动提单升级，「升级需手工」的前提不成立。放开 `^` 区间反而与配置的锁版策略相悖。维持现状，无动作。
