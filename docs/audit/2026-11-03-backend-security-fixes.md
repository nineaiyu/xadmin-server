# 后端与安全修复报告（2026-11-03）

> 依据：《xadmin 总审查文档（2026-11-03）》（工作区根 `docs/audit/2026-11-03-full-review.md`）
> 后端章节 + 安全章节中落在服务端的条目；详细证据见
> `xadmin-server/docs/audit/2026-11-03-backend.md`（后端源稿）与
> `docs/audit/2026-11-03-security.md`（安全源稿）。
> 范围：**仅 `xadmin-server` 仓库**；不提交 git；保留工作区其它未提交改动。
> 基线：v5.0.2 / `dev` 分支。修复期间全部门禁与测试在真 PG(55433)/真 Redis(56379) 测试栈执行。

## 〇、完成度总览

| 状态 | 条目 |
|------|------|
| **已修复（代码 + 守护测试）** | BE-M1、BE-M2、BE-M3、BE-L1、BE-L2、BE-L4、BE-L5、BE-L6、SEC-M2、SEC-L1、SEC-L2、SEC-L3 |
| **已修复（显式契约收口）** | BE-L3（按合并稿钦定的「公开则文档显式化即关闭」路径） |
| **未修复（登记原因）** | SEC-M1（HttpOnly：架构级变更，合并稿定为单独立项）；SEC-I1（属性澄清，无需修改）；SEC-L4（随 SEC-L3 CSP enforce 灰度自然关闭）；SEC-L5（环境提醒项，非仓库缺陷） |

验证基线（全部通过，证据见 §三各条「验证」）：全量 pytest（真 PG + 真 Redis，8 worker，9241 个收集用例，
退出码 0，本次新增 27 个守护用例）；`ruff format --check` / `ruff check` 0 告警；`mypy` 1052 文件 0 issue；
迁移漂移 `No changes detected`；跨 app import / 行数（文件 0 超限、函数 0 超限）/ 缓存键 / 台账编号 /
菜单标题 / i18n zh 词条 门禁全绿；`load_init_json --check` 0 error（种子未改动）。

---

## 一、后端修复项

### BE-M1【中】异步导入/导出任务补 `acks_late` —— 已修复 ✅

- **文件与行**：`system/tasks/__init__.py:186-190`（`async_export_data_task`）、
  `:252-255`（`async_import_data_task`）。
- **修复方式**：两任务补 `acks_late=True`（worker 崩溃 / 宿主重启 / 30min 硬超时杀进程后消息未 ack 即重投）。
  重投安全由实现体既有终态守卫幂等化（`task/services/_import.py:99-102`、`_export.py:179-181`
  对 SUCCESS/FAILURE/REVOKED 记录直接跳过；崩溃在途的外层事务随连接丢失回滚）。与邮件/Webhook 任务
  （`common/tasks.py:58,94`、`task/webhook_tasks.py:101`）口径对齐。
- **验证**：守护测试 `tests/unit/system/test_async_task_acks.py`（2 用例，仿
  `test_mail_tasks_declare_acks_late` 先例）+ 全量 pytest 通过。
- **需要部署配合**：无需；重启 `xadmin-server xadmin-celery-worker xadmin-celery-heavy` 生效（仓规常规动作）。

### BE-M2【中】Webhook 重试到期扫描兜底 —— 已修复 ✅

- **文件与行**：`task/webhook_tasks.py:238-271`（新增 `sweep_expired_webhook_retries` 周期任务，
  `@register_as_period_task(interval=300, module="webhook")`）；`system/tasks/__init__.py:46`
  （beat 注册引入）；单轮上限 `SWEEP_BATCH_LIMIT = 100`（`webhook_tasks.py:235`）。
- **修复方式**：周期任务扫描 `status=failed AND next_retry_at <= now` 的投递，经统一派发入口
  `dispatch_deliver_webhook` 重派——**代际号条件更新机制**（既有）天然防与在途 countdown /
  人工 retry 重复投递：broker 丢 countdown 消息后投递不再静默停摆（最长 5 分钟自愈）。
  只处理 `failed`：`pending`（在途）/ `success`（终态）/ `exhausted`（重试耗尽，走人工 retry + 告警）不参与。
  停用 webhook 模块时任务随之不注册（`module="webhook"`，周期任务归属守护测试锁定）。
- **验证**：`tests/unit/system/test_webhook_delivery_dispatch.py::TestRetrySweep`（5 用例：到期重派 /
  非参与态不扫 / 单轮批量上限 / 与人工 retry 并发不重复投递（真实投递链路，幂等键只出现一次））；
  `tests/unit/common/test_periodic_task_modules.py` 全过（module id 真实性 + 归属声明守护）。
- **需要部署配合**：重启需加 `xadmin-celery-beat`（新增周期任务经 beat 调度）；
  可选：把 `task.webhook_tasks.sweep_expired_webhook_retries` 加入 `MANUAL_RUNNABLE_TASKS`
  系统配置即可在任务中心手动触发巡检。

### BE-M3【中】分片上传 complete 长事务改两段式 —— 已修复 ✅

- **文件与行**：
  - `file/models/upload.py:147-159`（`UploadSession` 新增 `MERGING` 状态 + 两段式语义说明）；
  - `file/migrations/0003_alter_uploadsession_status.py`（纯 choices 扩展迁移，reverse 无副作用）；
  - `file/utils/upload_chunk.py:248-299`（`_claim_for_complete`：读校验 + CAS 认领 `PENDING→MERGING`，
    含认领超时残留原子退回续跑）、`:300-343`（`complete_session`：锁外合并 → 短事务收口
    `MERGING→COMPLETED`）、`:79-82`（`MERGE_STALE_SECONDS = 600`）、`:389-411`（`abort_session`
    新鲜 MERGING 拒绝 / 过期 MERGING 可作废回收）、`:467-497`（清理任务纳入 MERGING 残留，
    按认领时间判过期防误伤在途合并）；
  - `file/views/admin/file_chunk.py:198-209`（abort 视图把「合并中」转可读业务码而非 500）。
- **修复方式**：旧实现「行锁 + 全量合并 I/O + 落库」单事务 → CAS 状态机：
  1. **短事务 + 小 I/O 校验**（属主 / 扩展名 / magic 抽检 / 分片行完整性，失败口径与历史逐条一致：
     扩展名/magic 失败作废并清理分片、分片不完整保持 PENDING 可续传）；
  2. **CAS 认领** `PENDING→MERGING`（并发 complete 只有一方进入合并；崩溃残留按认领时间
     `updated_time < now-600s` 原子退回 PENDING 后续跑）；
  3. **锁外合并**（读分片 → md5/SHA-256 同步计算 → `store_upload_file` 落 is_tmp 临时行——
     收口失败/崩溃的孤儿由既有 `auto_clean_tmp_file` 按保留期回收，同 md5 同属主重试去重命中）；
  4. **短事务收口** `MERGING→COMPLETED`（挂接产物 + 删分片行；存储对象删除在提交后）；
     收口 CAS 失败即他方已收口（同 md5 同属主去重命中同一产物行），复用库内终态——幂等。
  数据库事务/行锁的持有时长与文件体积彻底解耦（BE-M3 的根因消除）；互斥与崩溃恢复语义均有覆盖。
- **验证**：`tests/unit/system/test_upload_chunk.py::TestTwoPhaseMerge`（7 用例：新鲜认领并发
  complete/abort 双拒且认领不被破坏；过期残留重认领后正常收口；过期残留 abort 回收；
  清理任务回收过期 MERGING / 不误伤新鲜 MERGING；收口竞态复用他方产物；合并期可重试失败退回
  PENDING 后重试成功）+ 既有全部分片协议用例（`test_merges_and_stores` / `test_md5_mismatch_...` /
  `test_content_hash_mismatch_...` / 清理任务用例）不回归。i18n 新文案已补 zh 译文
  （`locale/zh/LC_MESSAGES/django.po`：`Merging`→合并中、`Upload is being merged, please retry later`
  →上传正在合并中，请稍后重试），`tests/unit/common/test_i18n_po.py` 门禁通过；迁移漂移
  `No changes detected`。
- **需要部署配合**：需要执行迁移（`python manage.py migrate`，`file` 0003）；升级后 `xadmin-server`
  容器重启。**存量库无 in-flight 兼容问题**：迁移只加 choices，上线瞬间不存在 MERGING 行
  （旧代码不产该状态）。

### BE-L1【低】`CacheSortedSet.exist` rank 0 误判 —— 已修复 ✅

- **文件与行**：`packages/xadmin-common/common/cache/redis.py:123-126`。
- **修复方式**：`bool(zrank(...))` → `zrank(...) is not None`（zrank 对最低分成员返回 0，bool(0) 误判
  不存在）。成员取值约定保持不变（原值查询，与 push 存储形态一致）。
- **验证**：更新锁定怪癖的既有断言为修复后行为（`tests/unit/common/test_cache_redis.py:136-143`：
  rank 0 成员存在 / 缺失成员不存在）。按合并稿 §6.5「不换假绿」口径，此断言更新即守护测试。
- **需要部署配合**：无。

### BE-L2【低】`DataPermission.name` 改条件唯一（软删释放名称）—— 已修复 ✅

- **文件与行**：`system/models/permission.py:18-43`（`unique=True` → Meta.constraints 条件唯一
  `uniq_datapermission_name_active`，`Q(deleted_at__isnull=True)`）；
  `system/migrations/0002_alter_datapermission_name_and_more.py`（AlterField + AddConstraint，
  迁移 docstring 登记回滚口径：reverse 恢复全局唯一索引，若回收站存在同名活跃行将因唯一冲突失败——
  回滚前需先物理清除回收站同名规则）。
- **修复方式**：与 `UserRole` / `Menu` 的条件唯一范式对齐——软删进回收站的规则释放名称可被新规则
  复用；回收站恢复时同名冲突由 `RecycleBinAction` 既有 IntegrityError 分支逐行跳过（行为不变）。
- **验证**：`tests/unit/system/test_data_permission_scope.py::TestSoftDeletedNameReuse`（2 用例：
  软删后同名可重建、活跃集合内重复仍被拒）+ 既有回收站用例不回归；迁移漂移 `No changes detected`
  （新迁移已入链）；真 PG 测试库全量建库含该约束（全量 pytest 即为迁移正确性验证）。
- **需要部署配合**：需要执行迁移（`system` 0002）。

### BE-L3【低】媒体缩略图越权缝隙 —— 已修复（显式契约收口）✅

- **文件与行**：`packages/xadmin-common/common/utils/media.py:7-25`（模块契约段）、
  `docs/ops/storage.md` 新增 §5.1「媒体访问契约」。
- **修复方式**：按合并稿钦定的关闭路径（「公开则文档显式化即关闭」）把契约写死：
  `/media/` 原图与按 pk 解析的缩略图对**全部登录用户**可见、不按记录级数据权限过滤；
  `ProcessedImageField`（全仓仅头像类资产，URL 含 uuid5 随机段不可枚举）按站内公开语义设计；
  **敏感图片禁用该字段**，应存 `UploadFile` 体系（签名 URL + 归属校验 + download/preview 端点数据权限）。
  未做「缩略图单独叠加数据权限」的代码改动的工程依据（写入两处文档）：原图路径本不受记录权限
  管辖，单独收紧缩略图是无实效的伪防护，且会破坏跨部门头像渲染（聊天室 / 通讯录 / 审计）。
  若产品未来要求「模型图片按记录权限收敛」，需覆盖原图+缩略图两路径的独立改造项（文档已登记）。
- **验证**：外层防线既有守护测试维持全绿（`tests/unit/common/test_media_serve.py`、
  `test_media_signing.py`：鉴权 / safe_join / 内部目录 / 签名 URL）；文档门禁
  （`check_doc_paths` / `check_doc_index` / `check_doc_facts`）通过。
- **需要部署配合**：无；**需产品确认**：模型图片是否维持「站内公开」语义（当前证据：全仓该字段仅头像）。

### BE-L4【低】操作日志落库失败可观测 —— 已修复 ✅

- **文件与行**：`packages/xadmin-common/common/metrics.py:68-74`（新计数器
  `xadmin_operation_log_writes_total{result=ok|failed}`）、`:119-132`
  （`record_operation_log_write`）；`packages/xadmin-common/common/core/oplog_recorder.py:181-199`
  （`insert_operation_log` 成功计 ok、失败计 failed——旁路语义不变，失败仍只告警不抛出）。
- **修复方式**：与 `record_cache_request` 同款指标旁路（进程内计数器，/metrics 端点可拉取）；
  `failed` 标签即「审计链路静默丢失窗口」的监控/告警数据源（告警口径见 docs/ops/observability.md）。
- **验证**：`tests/unit/common/test_operation_log_middleware.py`（失败计 failed / 成功计 ok 两用例，
  含指标旁路失败静默断言）+ 全量 pytest。
- **需要部署配合**：无（METRICS_ENABLED 默认开启、端点令牌门控）；运维侧可按
  `rate(xadmin_operation_log_writes_total{result="failed"}[5m]) > 0` 配告警。

### BE-L5【低】`cached_method` 容量上限 —— 已修复 ✅

- **文件与行**：`packages/xadmin-common/common/decorators/cache.py:316-350`（新增
  `max_entries=512` 参数 + FIFO 淘汰：超限按插入序丢弃最旧的 1/4 批，先算后淘汰防异常误伤）。
- **修复方式**：与配置缓存 `L1_MAX_ENTRIES=512` 同款兜底语义；淘汰注释与使用限制同步更新。
- **验证**：`tests/unit/common/test_decorators.py::TestCachedMethod`（2 新用例：FIFO 淘汰生效 /
  极小上限下同 key 仍命中）+ 既有 TTL 用例不回归。
- **需要部署配合**：无。

### BE-L6【低/部署】compose Redis 显式 AOF —— 已修复 ✅

- **文件与行**：`docker-compose.yml:104-113`（redis `command` 增加
  `--appendonly ${REDIS_APPENDONLY:-yes}`）。
- **修复方式**：该实例承载缓存 / WS 通道层 / Celery broker 三个逻辑库，broker 消息（异步导入导出、
  Webhook 投递/重试）依赖持久化存活——RDB 崩溃丢消息窗口分钟级，AOF(everysec) 收敛到 ≤1s，
  与 BE-M1/M2 构成「消息队列可靠性」双保险（合并稿 §5.2 战役的部署侧一环）。注释登记当前 RPO
  与「纯缓存部署显式 `REDIS_APPENDONLY: "no"`」的退出通道。`docker-compose.prod.yml` 为 overlay、
  redis 服务继承基础栈——本改动对生产形态同样生效。
- **验证**：`docker compose config` 语义校验（compose 在本机仅测试栈运行，未实际重建 redis 容器——
  属部署侧动作，见下行）；改动为 command 数组追加一项，无语法风险。
- **需要部署配合**：**是**——生产/开发栈需 `docker compose up -d redis`（或 `docker compose up -d`）
  重建 redis 容器生效；已运行的存量数据卷自动兼容（AOF 目录 `appendonlydir` 首启生成，RDB 不受影响）。

---

## 二、服务端侧安全项

### SEC-M2【中】配置组合模板解引用敏感加密键（真实漏洞）—— 已修复 ✅

- **文件与行**：`packages/xadmin-common/common/core/config/base.py:21-27`（引入
  `is_sensitive_key` / `mask_sensitive_config_value`）、`:44-63`（`get_render_context`：模板引用
  敏感键一律掩码取值并告警留痕）、`:65-83`（`build_config_render_context`：敏感行不入上下文）。
- **修复方式（比安全源稿评估的利用面更彻底）**：审查复核时发现**普通登录用户**即可利用——个人配置
  （`UserPersonalConfig`，自服务可写键如 `PUSH_MESSAGE_NOTICE`）的值写 `{{ SCIM_TOKEN }}`，渲染上下文
  只含个人行（不含系统敏感键），模板引用经 `getattr(SysConfig)` 解密兜底解析直出明文（不需要
  「配置写权限」前提）。修复把渲染上下文收口为唯一泄露面：
  1. `build_config_render_context` 排除 `SENSITIVE_SETTING_KEYS` 全部行（其库内值本就是密文）；
  2. `get_render_context` 对模板引用的敏感键改为 **掩码取值**（整值键 → `MASK`；字段级键如
     `OAUTH_PROVIDERS` → 同构副本仅掩 `client_secret` 等声明字段），并打 warning 便于探测可观测；
  3. 三条路径统一收口：系统键引用系统敏感键（宿主键不敏感 → 出口掩码不触发，渲染产物即泄露面）、
     **个人配置模板自引用（低权面）**、字段级敏感键展开。
  非敏感键之间的组合渲染不受影响（守护回归用例）。
- **验证**：`tests/unit/common/test_config_render_value.py::TestSensitiveKeyNotDereferencedByTemplate`
  （6 用例：整值键经系统模板不泄露明文；**低权个人配置模板不泄露**；字段级键只掩声明字段、非敏感
  字段照常可组合；上下文直接排除敏感行；非敏感组合回归）+ 既有配置渲染 / configs API 用例不回归。
- **需要部署配合**：无（重启 web 容器生效）。**行为变化提示**：① 若存量库中存在「非敏感键值引用
  敏感键」的既有配置行，修复后其读值从明文变为掩码 + 一次 warning 日志（warning 的 file:line 可
  定位探测源）；② 系统配置缓存 TTL 长（30 天）且跨重启存活于 Redis——**修复前已被渲染成明文并
  写入缓存的行**，重启后仍会命中旧值直至该键缓存失效。处置：发现可疑引用行时直接改写/删除该行
  （写路径触发 `invalid_config_cache` 即时失效），无需等待 TTL。

### SEC-L1【低】CSRF Origin 校验默认值 —— 已处理（保持默认 + 自检/模板/文档三重收紧）✅

- **文件与行**：`config_example.yml:100-104`（`CSRF_REQUIRE_ORIGIN` 配置模板：适用场景、默认值
  兼容性依据、doctor 告警提示）；`system/utils/platform/security_doctor.py:36-49`
  （doctor 自检：非 DEBUG 且未开启 → WARN + 修复命令）；`system/management/commands/doctor.py:72`。
- **修复方式（按修复原则 2 的「无法安全翻转则保持默认 + 补启动校验/模板/文档」路径）**：
  **不翻转默认值**——`CSRF_REQUIRE_ORIGIN: false` 是已登记的兼容取舍（Cookie 通道 + Origin/Referer
  双缺失的写请求放行，覆盖面为携带 X-Token Cookie 的非浏览器脚本；现代浏览器不受影响，Bearer/PAT
  通道本就不经该分支），翻转会让存量部署的脚本类客户端直接 403。落实为部署侧三件套：
  doctor 上线自检告警（非 DEBUG 未开启即点名）、配置模板登记收紧指引、（见 §三验证）测试锁定
  三态行为。安全源稿与合并稿对该项的原判定即「配置已支持，无需改码，部署清单强调」。
- **验证**：`tests/unit/system/test_doctor.py::TestSecurityPostureCheck`（默认关闭 → 告警 + 收紧
  指引；已开启 → 通过；DEBUG 豁免）。
- **需要部署配合**：**是**——跨域 / 公网 / 高安全部署在 `config.yml` 显式
  `CSRF_REQUIRE_ORIGIN: true`（同源内网部署可保持默认）；上线跑 `manage.py doctor` 核对。

### SEC-L2【低】HTTPS/Secure Cookie 默认值 —— 已处理（同上路径）✅

- **文件与行**：`config_example.yml:85-93`（`SECURITY_HTTPS_ENABLED` 注释补「HTTP 直连 +
  文档站会话 Cookie 不带 Secure」的风险面点名）；`system/utils/platform/security_doctor.py:51-64`
  （doctor：非 DEBUG 未开启 → WARN，点名文档站会话组合）。
- **修复方式**：三态设计保持（TLS 终结于网关才可开启——`SECURE_SSL_REDIRECT` 在纯 TCP stream
  代理下会重定向循环，见 `server/settings/security_https.py` 模块注释），不盲翻默认；落实 doctor
  自检 + 模板风险面标注。
- **验证**：同上 `TestSecurityPostureCheck`（未开启告警 / 开启通过 / DEBUG 豁免）。
- **需要部署配合**：**是**——公网部署开启 `SECURITY_HTTPS_ENABLED: true`（或网关终结 TLS 后开启）。

### SEC-L3【低】Django 侧 CSP report-only —— 已处理（doctor 灰度推进 + 不盲切 enforce）✅

- **文件与行**：`system/utils/platform/security_doctor.py:66-91`（doctor：`CSP_MODE` 三态自检——
  report-only 提示观察期与切 enforce 路径、disabled 提示恢复、enforce 通过）。
- **修复方式**：**不盲切 enforce**——页面层已由 nginx 强制（E2E `test:e2e:csp` 守护），Django 侧
  只覆盖 `/api-docs/*` 等 Django 渲染面；在未跑隔离验证（`csp-report` 收集零违规）前翻转会让
  Swagger/Redoc（sidecar 静态资源）有被强 CSP 打挂的风险。落实为 doctor 把当前档位与灰度路径
  点名（report-only → 隔离验证 → 切 enforce），由部署按观察数据推进；切 enforce 后 SEC-L4 的
  api-docs 点击劫持理论面自然关闭。
- **验证**：同上 `TestSecurityPostureCheck`（含 `SystemConfig` 写 `CSP_MODE=enforce` 后通过的用例）。
- **需要部署配合**：**是**——观察期部署按 `docs/ops/deployment-csp.md` 灰度流程切 enforce。

### SEC-M1【中】认证 Cookie 非 HttpOnly —— 未修复（登记原因）

- **原因**：已登记的架构权衡（前端 `X-Token` 读写 + Bearer 回退契约），改 HttpOnly 需后端整体切
  会话制契约，属架构级变更——合并稿（§6.3）定为**单独立项**，本次范围（「不修改无关代码」）不含。
  现有缓解不放松：页面级 CSP 强制 + DOMPurify 净化链 + Markdown 禁原始 HTML + 气泡文本插值 +
  token 短生命周期 + 登出/强退即时拉黑（安全源稿逐项验证过）。
- **后续**：高安全客户可评估 BFF/网关层 HttpOnly 会话可选模式（合并稿建议）。

### SEC-L4 / SEC-L5 / SEC-I1 —— 无代码动作（登记）

- **SEC-L4**：api-docs `xframe_options_exempt` 的点击劫持理论面随 SEC-L3 切 enforce 自然关闭，
  无独立代码改动（豁免为内嵌文档功能所需）。
- **SEC-L5**：本地开发配置含真实密钥且 DEBUG=true——已验证不入库（git-ignore），属环境纪律提醒
  （开发栈仅内网可达），非仓库缺陷。
- **SEC-I1**：登录口令 AES「传输加密」密钥即临时令牌——安全属性澄清项（防嗅探不防 MITM，与项目
  注释口径一致），无需修改。

---

## 三、验证与门禁汇总

| 项 | 命令 / 结果 |
|----|-------------|
| 全量测试 | `uv run pytest -n auto -q`（真 PG 55433 / 真 Redis 56379 测试栈，8 worker）：**通过**，退出码 0，进度推进至 100%（9241 个收集用例，含本次新增 27 个守护用例） |
| 静态 lint | `ruff format --check`（1967 文件已格式化）/ `ruff check`：**0 告警** |
| 类型检查 | `mypy`：**Success: no issues found in 1052 source files** |
| 迁移 | 新增 `system/migrations/0002_*`、`file/migrations/0003_*`；`makemigrations --check --dry-run` = **No changes detected**（模型与迁移零漂移）；全量 pytest 的建库即两迁移在真 PG 的执行验证 |
| 行数门禁 | 文件 ≤500（1015 文件 0 超限）、函数 ≤100（0 超限）——修复过程曾超限（doctor 573 / upload_chunk 508 / complete_session 101），已按门禁「拆分」口径重构（安全自检下沉 `system/utils/platform/security_doctor.py`、`_claim_for_complete` 拆分），最终 500/500/约 60 行全绿 |
| 横切门禁 | 跨 app import（allowlist 18 / 契约缝 8 / 反向依赖 0）、`check_cache_keys --strict`、`check_ledger_ids`、`check_menu_titles`、i18n zh 词条（新增 2 条已登记）全部通过 |
| 种子 | `load_init_json --check` 0 error（本次未改种子） |
| 未运行 | E2E / 视觉基线（后端改动且未触前端契约，合并稿 §6.5 判定无需；前端仓不在本次范围）；k6 压测（大文件合并的事务时长改善属结构性修复，未做压测复演——已如实登记） |

**失败区分**：全量运行中无失败；若现场出现失败，判断口径为「先跑同一用例隔离复现 → 对照本次改动
文件清单」——本次改动面全部有专属守护用例先行通过。

## 四、部署侧确认清单（汇总）

1. **执行迁移**：`python manage.py migrate`（`system.0002`、`file.0003`）→ 重启容器
   `docker restart xadmin-server xadmin-celery-worker xadmin-celery-heavy xadmin-celery-beat`
   （新增周期任务，beat 必须重启）。
2. **重建 redis**：`docker compose up -d redis`（AOF 生效；存量 RDB 卷兼容）。
3. **上线自检**：`python manage.py doctor`——安全基线三条（CSRF / HTTPS / CSP）按部署形态确认；
   公网部署按告警提示收紧 `CSRF_REQUIRE_ORIGIN` / `SECURITY_HTTPS_ENABLED`，CSP 按灰度流程切 enforce。
4. **可选**：`MANUAL_RUNNABLE_TASKS` 加 `task.webhook_tasks.sweep_expired_webhook_retries`（任务中心
   可手动触发兜底巡检）；监控面板为 `xadmin_operation_log_writes_total{result="failed"}` 配告警。
