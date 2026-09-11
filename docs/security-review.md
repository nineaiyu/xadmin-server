# 安全自查清单（归档）

> 背景：半年规划 P5/T5.3「安全自查二期」。本文档归档每轮安全自查的范围、结论与遗留项，
> 后续自查在本文追加新章节，不另立文档。
> 关联：ADR-001（CSRF/JWT-only 决策）、docs/exception-handling.md（错误脱敏）、
> docs/architecture/permission.md（三层权限）。

## 二期自查（2026-09-06）

范围：Flower 认证收尾、X-Frame-Options、Referer 校验、上传类型校验复核。
越权矩阵测试（水平/垂直越权用例集入 CI）的开发侧完成于同日（见下节），实跑验证留待测试窗口。

### 1. Flower 任务监控认证 ✅ 本次收尾

| 项      | 内容                                                                                                                                                                                                                                                                           |
|--------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 风险     | 历史版本 `CELERY_FLOWER_AUTH` 默认值硬编码弱口令（`flower:flower123.` / `flower:flower` 双兜底），部署方不改即带弱口令暴露监控面板                                                                                                                                                                              |
| 处置     | ① `server/conf.py` 默认值改为空串；② `common/management/commands/services/hands.py` 移除 `or 'flower:flower'` 兜底；③ `services/flower.py` 启动守卫：未配置认证时仅允许绑定 `127.0.0.1`/`localhost`，绑定其他地址直接 `sys.exit(11)` 拒绝启动，未配置认证时不再向 flower 传空的 `--basic-auth=` 参数；④ `config_example.yml` 补充配置示例与说明 |
| 验收     | 全仓 grep 无 `flower123`/`flower:flower` 硬编码残留；生产部署必须显式配置 `CELERY_FLOWER_AUTH` 才能对外暴露监控面板                                                                                                                                                                                       |
| 面板访问链路 | 管理台经 `common/celery/flower.py` 代理访问，代理侧自动携带所配置的 basic-auth，前端无需感知                                                                                                                                                                                                            |

### 2. X-Frame-Options ✅ 无需变更

- `XFrameOptionsMiddleware` 已启用（`server/settings/base.py` 中间件链），未显式设置 `X_FRAME_OPTIONS`，取 Django 默认
  `SAMEORIGIN`，管理台页面不可被第三方 iframe 嵌套。
- 既有豁免均为有意保留：`common/swagger/views.py`（API 文档页）、`common/celery/flower.py`（Flower 代理页）需要以 iframe
  内嵌进管理台，属功能必需。
- 结论：默认防线有效，豁免面最小化，记录即可。

### 3. Referer 校验 ✅ 默认关闭属合理决策

- `REFERER_CHECK_ENABLED`（`server/conf.py` settings 段，实现在 `server/middleware.py`）默认 `False`。
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

| 项                              | 归属           | 说明                                                              |
|--------------------------------|--------------|-----------------------------------------------------------------|
| 越权矩阵测试（水平/垂直越权用例 ≥10 条入 CI）    | ✅ 开发完成       | 2026-09-06 交付 19 例（M01-M17）入 `tests/integration/`，实跑验证待测试窗口，见上节 |
| 上传 magic bytes 校验              | 按需           | 仅在扩展非图片类型上传时升级为必做                                               |
| client pnpm audit 高危清零（40 → 0） | P5/T5.1 升级窗口 | vue3-ts-jsoneditor 3.4.1 + 构建链刷新，独立分支 + 全量门禁；crypto-js 弃用替换评估   |
| server pip-audit               | ✅ 已清零        | 2026-09-06，见上节                                                  |

## 三期自查（2026-09-11）：JWT 专项审计（N5）

范围：签发/校验/吊销全链路（`common/core/auth.py`、`system/views/auth/`、
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
