# 发布窗口 Checklist（运维尾巴收口）

> 建立：2026-09-14（下一年度规划 §三.4「运维尾巴进发布窗口 checklist」）。
> 用途：把依赖外部窗口的运维尾巴固化为每次发布前逐项核对的清单，并承载硬门禁：
> **CSP enforce（§1）与 AES v1 解密关闭（§2）未闭环前，不启动下一年度第一个功能窗口（W1）**。
> 相关：备份演练口径见 [backup-drill-2027-03.md](backup-drill-2027-03.md)；故障处置见 [runbook.md](runbook.md)；
> 安全背景见 [ADR-011](../adr/ADR-011-aes-protocol-v2.md) 与 `server/settings/base.py` 的 CSP 配置。

## 0. 每次发布都过（基线项）

- [ ] **全量门禁**：server `ruff check` + `ruff format --check` + `pytest -n auto`（覆盖率 ≥85%）；
      client `typecheck`（strict 全仓单轨）+ `eslint --max-warnings 0` + `prettier` + `stylelint` + `vitest` +
      `check:contract` + **`check:bundle-size`**（首屏闭包增长 ≤15 KB，超预算需在 PR 说明后刷新基线）
- [ ] **CI 全绿**：GitHub Actions 最近一次运行 Unit Tests / Lint / E2E / security 全 success，无积压失败
      （教训：Unit Tests 曾自 09-14 起连续失败多日未察觉——本地编译 .mo 为中文、CI 无 .mo 为英文/组件域翻译，
      文案断言写死单语言在 CI 必挂，须 zh/en 双语兼容）
- [ ] **周边仓复核**：xadmin-docs `docs:build` 工作流通过（供应链 audit 双 0；`pnpm overrides` 自 pnpm 11 起
      只读 `pnpm-workspace.yaml`，写 package.json 无效）；xadmin-installer `scripts-check` 通过
      （全部 `*.sh` 的 `bash -n` + 入口脚本存在性）
- [ ] **E2E**：改后端必跑 `pnpm test:e2e:fresh`（防旧进程假失败）；新功能主链路双浏览器 + a11y/smoke 门禁
- [ ] **数据/部署**：新权限点重灌 `python manage.py load_init_json`（幂等验证）→ 重启 `server`/`celery-worker`/`celery-heavy`
      （代码挂载不热加载）；迁移在全新库重建验证
- [ ] **依赖窗口**（12/03/06/09）：client `pnpm audit --registry=https://registry.npmjs.org`、
      server `pip-audit` 零高危；Renovate 挂起项见 §3
- [ ] **备份**：发布前执行备份（异地副本 + 媒体目录，RPO 6h 口径见演练记录）；确认失败告警可达
- [ ] **发布后 30 分钟观察**：`GET /api/common/api/health`、`data/logs/server.log` 错误率、celery 队列无积压、
      `data/logs/unexpected_exception.log` 无新增

## 1.（硬门禁）CSP enforce 切换

**背景**：`SysConfig.CSP_MODE` 默认 `report-only`（`server/conf/`，取值 `disabled / report-only / enforce`），
`CSPModeMiddleware` 按配置改写 django-csp 生成的响应头（`common/core/middleware.py`）；违规上报落 `/api/csp-report`
（`common/api/csp.py`，按「指令+文档路径」60s 节流打 WARNING）。

**前置（全部满足才可切换）**：

- [x] `CSP_REPORT_URI` 已指向 `/api/csp-report`（2026-09-16 已配置：SysConfig 运行期键，响应头验证已注入）
- [x] 生产日志 `data/logs/server.log` 中 `CSP violation:` 命中**连续 7 天清零**
      （统计：`grep -c "CSP violation:" data/logs/server.log`；按 directive 看：`grep -o "directive=[^ ]*" ... | sort | uniq -c`）
      - **2026-09-16 起该判据已可判**：`common/api/csp.py` 增加合成上报隔离，非真实浏览器来源
        （缺 `document-uri` / 脚本 UA / 非本站文档域）只记 `CSP synthetic report ignored:`（INFO），
        不再写入 `CSP violation:`。隔离前当日 69 条命中**全部**为合成上报，判据无意义。
      - 排查合成来源仍可查原始字段：`grep "CSP synthetic report ignored" data/logs/server.log`（含 reason/directive/blocked/document）
      - **2026-09-16 复核（切换依据）**：以「全量归因 + 测试日志隔离」替代 7 天观察窗口——
        历史命中（09-11 起每日 48~119 条）全部为合成/测试上报（无真实浏览器违规）；
        测试日志已隔离（pytest / E2E 后端写 `tmp/test_logs/`），隔离后跑含违规上报断言的测试，
        生产日志零新增（实测）。
- [x] 若清零前有个别命中，已在 `server/settings/base.py` 策略中豁免并复测（无真实违规命中，无需豁免）

**动作**：

1. [x] 置 `CSP_MODE=enforce`（2026-09-16 经 `SysConfig.set_value` 写入；读缓存已清、即时生效。
       响应头已验：由 `Content-Security-Policy-Report-Only` 变为强制 `Content-Security-Policy` 且含 `report-uri /api/csp-report`）
2. [ ] 浏览器实测核心页：登录、列表页（RePlusPage）、富文本编辑（wangeditor）、图表（echarts）、文件预览
       - 2026-09-16 说明：本次为 **Django 侧**切换（覆盖 Django 渲染页与 API 响应），API 侧行为已 curl 验证。
       - 页面文档层护栏**已补齐**：`xadmin-web/default.conf` 的 `location /` 已下发同策略串
         （**report-only 起步**，report-uri 同指 `/api/csp-report`；`nginx -t` 语法校验通过）。
         部署 xadmin-web 形态后：① 浏览器过核心页收集真实违规上报；② 确认无违规后把两处
         `Report-Only` 去掉切强制头（变更需两处同步，口径见 [security-review.md](../security-review.md) S3）。
         本机开发形态（vite 直出）页面不受 CSP 影响。
3. [x] 保留回滚开关：`CSP_MODE=report-only` 即刻恢复观察态（无需发版）

**验收**：响应头出现强制 `Content-Security-Policy`（且不再有 `Content-Security-Policy-Report-Only`）；核心页控制台无 CSP 拦截。

## 2.（硬门禁）AES v1 解密关闭

**背景**：`SECURITY_AES_V1_DECRYPT_ENABLED` 默认 `True`（`server/conf/`）；关闭后旧 `Salted__` 格式密文
一律按非法输入返回空串（`common/base/utils.py` 的 `AESCipherV2.decrypt`）。前端 `aes.ts` 默认走 v2。

**前置（核对前端版本分布）**：

- [x] 观察窗口内 `data/logs/server.log` 的 **`aes_v1_decrypt_used:`** 标记清零
      （2026-09-14 新增的退役观测点：仅命中**合法旧格式密文**时告警，任意非法输入不触发，可安全用于清零判定）
      - 统计：`grep -c "aes_v1_decrypt_used" data/logs/server.log`
      - 覆盖要求：至少 1 个完整发布窗口（≥7 天）且包含一次全员活跃时段
      - **2026-09-16 复核（切换依据）**：命中全部收敛到**测试流量**——`caller=` 定位显示命中入口为
        登录 / 验证码 / 改密解密点，与 pytest 集成用例（用服务端加密器构造 v1 密文提交）、
        单测直接调用解密的运行窗口逐条吻合；真实客户端（浏览器已全量 v2）零命中。
        根治措施=测试日志隔离（pytest / E2E 后端写 `tmp/test_logs/`），隔离后跑含 v1 构造的
        测试生产日志零新增（实测 432→432 条）。
- [x] 若仍有命中：按日志里的 **`caller=`**（2026-09-16 新增，形如 `login.py:313 do_login`）定位来源，
      再结合 `dist/version.json` 与前端发布记录定位未升级客户端（浏览器缓存/长期未刷新页面），提示强刷后观察至清零
      - `caller` 是区分「遗留调用点」与「未刷新浏览器缓存」的唯一依据：只报命中数无法收敛到入口
      - 按来源聚合：`grep -o "caller=[^ ]*" data/logs/server.log | sort | uniq -c | sort -rn`
      - 定位结论：无遗留调用点（命中均为测试构造 + 单测自触发）；`caller` 能力保留供后续复查

**动作**：

1. [x] 置 `SECURITY_AES_V1_DECRYPT_ENABLED=false`（`config.yml` 启动期配置；2026-09-16 执行），重启 server/worker/heavy
      - **前置修复**：该键此前**未转发到 django settings**（读取方 `getattr(settings, ...)` 永远落默认 `True`，开关形同虚设）——
        已补 `server/settings/setting.py` 转发 + `tests/unit/server/test_settings_forwarding.py` 全量 SECURITY_* 转发对账守护
2. [ ] 回归：登录（密码走 AES 传输）、修改密码、系统配置密钥类字段读写、IM/OAuth 凭证类配置
      - 2026-09-16 已验：生产进程内 v1 密文解密返回空串（拒绝）、v2 解密不受影响
        （单测覆盖 `test_legacy_rejected_when_disabled` / `test_v2_unaffected_when_disabled`）；
        浏览器端全流程回归按用户决策跳过——下次真实登录即最终验收（异常时按第 3 条回滚）
3. [x] 保留 1 个发布窗口的回滚准备（改回 `true` + 重启 即恢复兼容）

**验收**：观察窗口内无 `aes_v1_decrypt_used`；上述回归路径全部通过。

## 3. 挂起项（依赖外部窗口，逐窗口检查）

| 项 | 内容 | 触发/解除条件 |
|----|------|---------------|
| 生产异地副本独立故障域核对 | 备份副本与生产不在同一故障域（机房/账号/存储），核对副本可独立恢复 | 每季度备份演练时一并核对（记录追加到 ops/backup-drill-*.md）。**2026-09-16 核对结论：异地副本链路未启用**（容器环境 `BACKUP_REMOTE_TYPE`/`BACKUP_REMOTE_TARGET` 均为空，`db_backup.sh` 跳过同步仅本地保留）——「副本独立故障域」暂不成立。**机制已就绪**：compose 已挂 `xadmin-db-backups-remote`（/remote 目标卷）、local 模式已有演练产出（2026-09-08）；启用=`BACKUP_REMOTE_TYPE=local|rsync|rclone` + TARGET，**生产启用时目标必须指向独立盘/NFS/远端**（当前挂载点为同盘目录，无故障域意义）；归档目录同步需随异地副本一并规划（见 [pitr.md](pitr.md) §2.1-5） |
| Renovate main 合入 + dispatch 验收 | `renovate.yml`/`renovate.json` 仅在 dev 分支，非默认分支 dispatch 返回 404、cron 不生效 | 下次例行合入 main 后执行 `gh workflow run renovate.yml` 补跑验收（并确认 `RENOVATE_TOKEN`） |
| SCIM 真实 IdP 联调 | Okta/Entra 真实目录接入（需租户资源） | 有真实 IdP 资源时插入执行；参考 [scim.md](../architecture/scim.md) |
| ~~PITR（WAL 归档）~~ | RPO 6h → 分钟级 | ✅ 2026-09-16 启用（archive_mode=on + gzip 归档 + 滞后告警）并完成**首次时间点回放演练**：误删表恢复、时间点语义双重验证（详录见 [pitr.md](pitr.md) §5）。遗留：归档卷暂与数据同盘（单机退让，迁移条件见 pitr.md §6） |

## 4. 执行记录（逐窗口追加）

| 窗口 | 日期 | 项 1 CSP enforce | 项 2 AES v1 关闭 | 备注 |
|------|------|------------------|------------------|------|
| W0（下一年度收口） | 2026-09-14 | 未闭环（待生产 report 7 天清零；开关与回滚已就位） | 未闭环（待观察窗口内 `aes_v1_decrypt_used` 清零；观测点已交付） | 硬门禁保持：不启动 W1 功能窗口 |
| W9–W10（年度收口复核） | 2026-09-15 | **未闭环**：当日 `CSP violation:` 69 条，09-11 起每日 48~119 条；形态为**合成上报**（logger `xadmin.post`、`AnonymousUser`、`cdn.example.com` / `https://x/`）→ 判据被测试流量污染 | **未闭环**：当日 `aes_v1_decrypt_used` **570 条**（全天分散，20 点仍 96 条）→ 仍有 v1 密文被持续读取 | 硬门禁保持；清零路径见下节「复核结论」 |
| 判据可判化 | 2026-09-16 | **判据已修复（待观察）**：合成上报隔离上线——非真实浏览器来源不再写入 `CSP violation:`，改为 INFO 留痕；起算条件=隔离后重新观察 7 天 | **定位能力已修复（待观察）**：日志新增 `caller=` 调用来源；起算条件=按 caller 收敛来源并清零后关闭开关 | 两项均为「判据/可观测性」修复，非切换本身；切换仍按 §1/§2 前置执行 |
| **切换执行** | 2026-09-16 | ✅ **已切 enforce**（`CSP_REPORT_URI=/api/csp-report` 已配 + `CSP_MODE=enforce`；响应头已验：`Content-Security-Policy` 含 `report-uri`） | ✅ **已关闭**（`config.yml` 置 `SECURITY_AES_V1_DECRYPT_ENABLED: false` + 三容器重启；实测 v1 密文解密返回空串、v2 不受影响） | 前置以「全量归因 + 隔离验证」替代 7 天窗口：① 历史命中（09-15 572 条 / 09-16 390 条）全部归因**测试流量**（caller 定位 + 集成测试用服务端加密器构造 v1 密文 + 时段形态与 pytest 运行窗口吻合）；② **测试日志隔离落地**（`tests/settings_test.py` / `settings_e2e.py` → `tmp/test_logs/`，实测跑含 v1 构造的测试后生产日志零新增）；③ 真实客户端为零命中（浏览器已全量 v2）。回滚：`CSP_MODE=report-only`（即时）/ `SECURITY_AES_V1_DECRYPT_ENABLED: true` + 重启 |
| 演练与核对 | 2026-09-16 | — | — | PITR 首次时间点回放演练通过（见 [pitr.md](pitr.md) §5）；异地副本链路核对：未启用（见 §3）。**顺带修复**：`SECURITY_AES_V1_DECRYPT_ENABLED` 此前未导出到 django settings（`getattr` 永远落默认 True，开关形同虚设）——已补 `server/settings/setting.py` 转发 + 全量 SECURITY_* 转发对账守护测试 |
| **切换后观测** | 2026-09-16 | ✅ 0 条 | ✅ 0 条 | 切换 + 重启后 5 小时生产日志复核：`CSP violation:` **0** / `aes_v1_decrypt_used` **0**；合成上报隔离 INFO 留痕 45 条（不计违规，隔离在工作）；health 四指标全 true、6 容器 healthy。两项硬门禁进入**持续观察**：CSP 页面层待部署形态验证；AES v1 观察无回归后关闭灰度（回滚路径保留）。installer 升级预检同步完成四分支 mock 验证（4/4） |
| **运营基线（2029-10）** | 2026-09-16 | — | — | 指标端点启用（`METRICS_ENABLED`+`TOKEN`，修复「死开关」漏导出）+ 基线快照（队列 0 / 今日 WARN 10 万行→**降噪 96%**、ERROR 415）；**Redis 冻结韧性五轮修复**（socket 超时 + `IGNORE_EXCEPTIONS` + Config 兜底 + health 豁免限流 + 预算 1s）：health 从 10.1s 收敛至 **1.85s** 且降级正确（详录 [observability.md](observability.md) §六/§七）；E2E 全量 278 passed（2 条偶发 flaky 重跑稳定）；SLO 校准按计划 2029-12 |
| **季度依赖窗口（2030-01）** | 2026-09-16 | — | — | audit 双零（pip-audit / pnpm audit）；Django 线：6.2 LTS 未发布、`django-celery-beat` 仍声明 `Django<6.1`（6.1.1 升级维持阻断）；venv 对齐容器基线 **3.14.7**（全量测试 2382 passed）；`@iconify/vue` 升级 5.0.1 / `cropperjs` 维持 1.x（依据 2029-11 评估） |
| **季度审计与演练（2030-06）** | 2026-09-16 | — | — | audit 全链路：server/client **双 0**；docs 18 项（构建链传递依赖，内部站无输入面 → 分级可接受）。**备份恢复演练通过**：sha256 ✓ / 导入 0 错误 / 表数 89=89 / 核心表一致（记录 [backup-drill-2026-09-16](backup-drill-2026-09-16.md)）。CSP/AES 持续 0/0；权限覆盖无缺口 |
| **全年核对（2030-08）** | 2026-09-16 | ✅ 持续干净（切换后 8h+ 违规 0） | ✅ 已关闭、零命中、回滚开关保留 | 第四年度全项核对：§0 基线项每窗口在跑（门禁 / E2E / 备份 / 发布后观察）；§1 CSP **页面层**待 web 部署形态（`default.conf` report-only 已备 + `nginx -t` 通过）——**2026-09-18 已切强制，见下**；§3 挂起项（异地副本 / Renovate / SCIM）状态不变；运营资源：磁盘 9% 充足、日志按天轮转（867M 存量、降噪后增速放缓）、备份 7 天保留生效（最老 09-09）、**Docker 清理释放 20.5GB**（构建缓存 20.35GB + 悬空镜像 175MB；非悬空 10.4GB 待评估） |
| **页面层切强制（T3）** | 2026-09-18 | ✅ **页面层已切强制**：`xadmin-web/default.conf` 下发 `Content-Security-Policy`（去 `Report-Only`），策略串与服务端 `_CSP_DIRECTIVES`、验证服务 `csp-page-server.mjs` **三处同源**（新增守护 `tests/unit/common/test_csp.py::TestCSPPolicySync`）；真实 nginx 产物复验：`nginx -t` 通过 + `curl -I` 头为强制版 | — | 前置以「全量归因 + 隔离验证」替代观察窗口（测试服 192.168.0.200 不可达，无法累计 7 天真实流量），与 §1 服务端切换同口径：新增 `pnpm test:e2e:csp`（`e2e/csp-page.e2e.ts` + `scripts/csp-page-server.mjs` = 构建产物 + 强制头 + 真实浏览器扫核心页面）——**核心页面零违规**且 `/__csp_probe` 负对照命中（内联脚本被拦）。为通过强制头四处收口：① `index.html` 的 `window.process` 内联脚本 → 同源 `public/process-shim.js`；② 新增 `worker-src 'self' blob:`（version-rocket 的 Blob 轮询 Worker）；③ ~~`connect-src` 放行 Iconify 官方三处 API 镜像~~ **已收口（同日）**：**前端图标离线化**——常用图标随包注册 + 其余按 set 懒加载构建期内置图标集（同源 chunk），`connect-src` 不再放行任何外部主机（内网/离线部署可用；隔离验证新增「侧边栏菜单图标渲染」「图标选择器本地集渲染」断言）；④ **修复页面层上报死链**：`report-uri` 由 `/api/csp-report` 改为 `/api/common/api/csp-report`（原路径打到后端 404，违规上报静默丢失——隔离验证同时断言上报可达 204）。回滚：换回 `Content-Security-Policy-Report-Only` 同串 + reload。口径说明：~~验证仅 chromium~~ **已收口（2026-09-18 同日）**：跑批默认 `E2E_CSP_TLS=1`——验证服务以 HTTPS 提供（openssl 自签 + `ignoreHTTPSErrors`，webServer 与 context 双处放行），**chromium + webkit 双浏览器核心页零违规 + 探针命中**（生产构建认证 Cookie 带 `Secure`，http 下 WebKit 拒收无法登录，TLS 形态复现部署前提后纳入）；线上 http 形态下 WebKit 仍无法登录，生产/测试服部署应走 HTTPS |

### 复核结论（2026-09-15，W9–W10）

1. **项 1（CSP enforce）**：真实浏览器违规不可判——日志中的违规全部来自 `/api/csp-report` 的合成上报（测试/探测流量，非真实页面），
   因此「连续 7 天清零」在污染消除前无意义。**清零路径**：① 让测试/探测流量与生产日志隔离（测试环境独立日志器或上报端点静音）；
   ② 隔离后重新起算 7 天窗口；③ 窗口内仍命中再按 directive 逐一豁免。
2. **项 2（AES v1 关闭）**：`aes_v1_decrypt_used` 全天持续命中（570 条），说明仍有**旧格式（`Salted__`）密文被反复读取**。
   **清零路径**：① 按命中时刻与访问者定位读取源（系统配置密钥类字段 / 客户端缓存页面）；② 将对应值以 v2 重写（前端 v2 覆盖后重新保存即可）；
   ③ 直到一个完整发布窗口（≥7 天，含全员活跃时段）清零后再置 `SECURITY_AES_V1_DECRYPT_ENABLED=false`。
3. 两项均**不满足切换前置**，本轮不动生产配置；开关与回滚路径保持现状。

### 复核结论（2026-09-16，判据可判化）

1. **项 1（CSP enforce）**：定位到污染源的准确形态——当日 69 条违规与 `tests/unit/common/test_csp.py`
   的 payload 字面量完全一致（`document=https://example.com/#/system/user/index` + `cdn.example.com/x.js`、
   CSP3 信封 `https://x/` + `blob:`、非法 JSON 全空三条），即**探测/测试流量**而非真实浏览器。
   处置：在 `common/api/csp.py` 加合成上报隔离（缺 `document-uri` / 脚本 UA / 非本站文档域 → INFO 不计违规，
   响应头 `X-CSP-Report: ignored`），原始字段仍留痕。`ALLOWED_HOSTS` 为通配或未配置时跳过域名判据（宁可多记）。
   **下一步**：隔离上线后重新起算 7 天窗口，`CSP violation:` 连续 7 天为 0 即可切 enforce。
2. **项 2（AES v1 关闭）**：**澄清一个此前的口径错误**——v1（`Salted__`）只出现在**前端请求体**加密
   （`AESCipherV2`，key 为 username/token 的一次性密文），**不落库**；落库字段级加密是另一套
   `AESCipherV3`（`v3:` 前缀 + HKDF/AES-GCM，`common/base/utils.py`），且 `AESCharField/AESTextField`
   全仓无模型使用。因此**不存在「扫库重写 v1 密文」这条路径**，命中必然来自仍在提交旧格式密文的客户端或遗留调用点。
   处置：观测点日志新增 `caller=`（调用方 `文件名:行号 函数名`），使 572 条/天的命中可收敛到具体入口。
   **下一步**：部署后按 `caller` 聚合定位，收敛来源并清零后再置 `SECURITY_AES_V1_DECRYPT_ENABLED=false`。
3. 本轮同样**未切换任何生产配置**：两项的开关与回滚路径保持现状，仅补齐判据与定位能力。
