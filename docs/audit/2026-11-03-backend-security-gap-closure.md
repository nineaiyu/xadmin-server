# 后端与安全查漏补缺复核报告（2026-11-03）

> 依据：总审查 `docs/audit/2026-11-03-full-review.md`（工作区根）、后端源稿
> `2026-11-03-backend.md`、修复报告 `2026-11-03-backend-security-fixes.md`、
> 收尾报告 `../docs/audit/2026-11-03-remaining-fixes.md`（工作区根）。
> 本轮**不采信任何报告中「已修复」的表述**，逐条回到代码验证；对全部条目
> （含已修复、观察项、暂缓项、待确认项）复核真实状态，并对「重复出现的类问题」
> 做机制化收口。
> 范围：`xadmin-server` 仓库（含 `docs/**`）；未提交 git，未回滚工作区既有改动。

## 〇、结论总览

| 状态 | 条目 |
|------|------|
| **复核属实（修复在位）** | BE-M1、BE-M2、BE-M3、BE-L1、BE-L2、BE-L4、BE-L5、BE-L6、SEC-M1、SEC-M2、SEC-L1、SEC-L2、SEC-L3、SEC-L4、SEC-L5、SEC-I1 |
| **复核发现缺口并补做** | ①BE-L4 告警出口（指标在但 observability.md 未登记，修复报告引用的口径不存在）；②BE-L6/BE-M2 部署校验（compose 改了但 doctor 不查——AOF 档位与 beat 存活两项自检本轮补齐）；③SEC-M1 消费点（token 读取分散两处——本轮统一单一入口 + 静态守护）；④台账编号泄漏（`settings_defaults.py` 注释带决策编号，门禁拦截后修复） |
| **观察项落地** | 操作日志失败告警出口、Redis AOF doctor 校验、beat 存活 doctor 校验、`handle_db_connections` 双 INFO 降级；锁排队 miss 监控（既有指标已覆盖，复核确认） |
| **机制化新增** | Celery `acks_late` 静态门禁（`check_task_acks.py`，59 任务全量归类）、认证 Cookie 单一读取入口（`read_auth_cookie` + 全仓静态守护）、敏感配置键注册表全量渲染守护（新增键自动纳入） |
| **需人工/部署确认** | 见 §五（产品语义 1 项、部署侧 5 项、浏览器回归 2 项） |

**验证基线**（全部真实运行，证据见 §四）：全量 pytest **8042 收集 / 8034 通过 + 8 跳过 / 0 失败**（真 PG 55433 / 真 Redis 56379，8 worker，exit 0）；ruff format --check + check 0 告警；mypy 1054 文件 0 issue；迁移漂移 `No changes detected`；全部门禁脚本（含新增 `check_task_acks`）与文档门禁逐项通过。

---

## 一、逐项复核清单

> 「实际现状」列全部来自本轮亲自读码 / 运行验证，不引用修复报告原文。

### 1.1 后端中级（3 项）

| 条目 | 级别 | 原结论 | 实际现状 | 处理 |
|------|------|--------|----------|------|
| BE-M1 | 中 | 已修复（导入/导出补 `acks_late`） | **属实**：`system/tasks/__init__.py` 两任务均 `acks_late=True` + 幂等口径注释；守护测试 `test_async_task_acks.py` 在位 | ✅ 复核通过；另做机制化（见 §二.1：新门禁扫描出 59 个任务全量归类，grep 漏掉的 4 个多行装饰任务也被 AST 扫描捕获） |
| BE-M2 | 中 | 已修复（到期扫描兜底任务） | **属实**：`sweep_expired_webhook_retries`（interval=300, module="webhook"）在位，`system/tasks/__init__.py:46` 显式引入注册；`TestRetrySweep` 5 用例 + `test_periodic_task_modules` 模块归属守护在位；派发统一走 `dispatch_deliver_webhook`（代际号防重） | ✅ 复核通过；根因「beat 未跑时兜底扫描自身也停」本轮补 doctor 存活自检（见 §二.4） |
| BE-M3 | 中 | 已修复（两段式合并） | **属实**：`_claim_for_complete`（读校验 + CAS `PENDING→MERGING`）→ 锁外 `_merge_and_store` → 短事务收口 CAS `MERGING→COMPLETED`；`abort_session` 对新鲜 MERGING 拒绝、过期可回收；清理任务按认领时间判过期；`TestTwoPhaseMerge` 7 用例在位 | ✅ 复核通过 |

### 1.2 后端低级（6 项）

| 条目 | 级别 | 原结论 | 实际现状 | 处理 |
|------|------|--------|----------|------|
| BE-L1 | 低 | 已修复（`exist` rank 0 误判） | **属实**：`cache/redis.py` 已改 `zrank(...) is not None`，锁定怪癖的既有断言已更新为修复后行为（非「只记录未修」） | ✅ 闭环 |
| BE-L2 | 低 | 已修复（条件唯一约束） | **属实**：`DataPermission.Meta.constraints` 条件唯一（`deleted_at__isnull=True`）+ 迁移 `system/0002`；`TestSoftDeletedNameReuse` 在位 | ✅ 闭环 |
| BE-L3 | 低/需产品 | 已修复（显式契约 + ADR-102） | **属实**：`media.py:20-24` 契约段 + `docs/ops/storage.md` §5.1 + ADR-102（站内公开语义与触发条件）均在位；外层防线守护测试全绿 | ⏳ 维持——**产品最终确认仍待人工**（当前证据：全仓 `ProcessedImageField` 仅头像类资产） |
| BE-L4 | 低 | 已修复（失败计数指标） | **部分属实，发现缺口**：`xadmin_operation_log_writes_total{result=ok/failed}` 指标与 `record_operation_log_write` 在位、两用例在位；**但修复报告声称「告警口径见 docs/ops/observability.md」——该文档既没登记指标也没登记告警行**（复核时 grep 零命中），告警出口实际未闭环 | 🔧 本轮补齐：observability.md 指标清单 + 告警覆盖清单各一行（`increase(...{result="failed"}[5m]) > 0`，P2 当班处置） |
| BE-L5 | 低 | 已修复（`cached_method` 容量上限） | **属实**：`max_entries=512` + FIFO 淘汰（先算后淘汰），`TestCachedMethod` 2 新用例在位 | ✅ 闭环 |
| BE-L6 | 低/部署 | 已修复（compose AOF） | **部分属实，发现缺口**：`docker-compose.yml` redis `command` 含 `--appendonly ${REDIS_APPENDONLY:-yes}`（prod overlay 继承生效）；**但部署侧无可执行校验**——任务要求「doctor 是否检查」，原实现 `_check_redis` 只测读写不查持久化档位 | 🔧 本轮补齐：doctor 新增 `report_redis_persistence`（`CONFIG GET appendonly`，自管部署关掉即告警 + 修复指引；FakeRedis 不支持则跳过） |

### 1.3 服务端安全项

| 条目 | 级别 | 原结论 | 实际现状 | 处理 |
|------|------|--------|----------|------|
| SEC-M1 | 中 | 双模式全交付（开关默认关） | **属实且消费点复核无遗漏**：浏览器面全部经默认 DRF 认证链（Cookie 回退生效）——WS（`server/asgi.py` 逐 backend 走 `DEFAULT_AUTHENTICATION_CLASSES`）、媒体（`media_serve` 显式 `CookieJWTAuthentication`）、SSE（`message/views_ai.py` 无自定义认证类）、flower 代理（GenericAPIView 默认链）、OAuth authorize/approve（`request.user` + JSON-only 收紧）；刷新 / 登出 / 退出模拟三端点 refresh 双读在位；**机器面（PAT / SCIM / OAuth token / metrics 端点）header-only 属设计而非遗漏**。剩余缝隙：token 读取分散两处（内核认证类读 X-Token + identity 读 X-Refresh-Token，各自 `parse_cookie`） | 🔧 本轮收口：新增内核单一入口 `read_auth_cookie`，三处消费统一 + 全仓静态守护（见 §二.2）；另修复复核中发现的台账编号泄漏（`settings_defaults.py:61` 注释含决策编号，`check_ledger_ids` 拦截后改为语义描述） |
| SEC-M2 | 中 | 已修复（渲染上下文收口） | **属实**：`get_render_context` 对敏感键掩码取值 + warning 留痕、`build_config_render_context` 排除敏感行；6 条守护用例在位 | 🔧 本轮泛化：注册表**全量参数化**守护（见 §二.3）——新增敏感键无需改测试即自动纳入 |
| SEC-L1/L2/L3 | 低 | doctor 三态自检 | **属实**：`security_doctor.py` 三条目 + `TestSecurityPostureCheck` 4 用例在位 | ✅ 闭环（收紧动作属部署侧，见 §五） |
| SEC-L4 | 低 | CSP enforce 灰度步骤 + 守护用例 | **属实**：`test_api_docs_prefix_excluded_in_enforce_mode` 在位（enforce 下 api-docs 零 CSP 头断言）；灰度步骤文档化 | ⏳ 待部署执行（观察期数据 + 人工点开 Swagger/Redoc） |
| SEC-L5 | 低/环境 | 运维文档交付 | **属实**：`docs/ops/dev-stack-network.md` 已登记索引 | ⏳ 待各部署现场执行自检 |
| SEC-I1 | 信息 | 无需修改 | **属实**（属性澄清项） | ✅ 无动作 |

### 1.4 观察项（后端 5 项逐条确认）

| # | 观察项 | 复核结果 | 处理 |
|---|--------|----------|------|
| 1 | 锁排队 65s 上限建议监控 miss | **已可观测**：`xadmin_cache_requests_total{result=hit/miss}` 既有（`MagicCacheData` 命中/未命中计数），observability.md 指标清单已登记 | ✅ 无需代码改动 |
| 2 | 批量导入整包单事务 | 有意取舍（「失败率超限整体回滚」语义强绑定），代码注释在位 | ✅ 维持（量级变化时再评估逐行提交 + 补偿） |
| 3 | 分片汇总靠 `cache.len()==task_count` | **守卫在位**：分片异常必须落失败结果（否则汇总挂起）的注释 + 实现在 `common/tasks.py:265-270`；Redis 驱逐的 24h TTL 兜底已注释声明 | ✅ 维持（重做汇总机制属独立改造，无现实故障证据） |
| 4 | `username` 全局唯一 | Django `auth.E003` 约束的已知权衡，序列化器可读提示在位 | ✅ 维持 |
| 5 | `handle_db_connections` 双 INFO | **现状比观察项描述更好**：全仓 grep 该装饰器已无调用点（celery 连接回收由信号路径承担）；保留为内核公共 API | 🔧 本轮降级：两行 INFO → debug + docstring 注明现状（未来复用零噪音） |

### 1.5 「未修复」4 项的收口确认

- **SEC-M1**：本轮完成（双模式已在前一批交付 + 本轮消费点复核 + 读取入口机制化收口），详见 §1.3 与 §二.2；
- **SEC-L4**：随 SEC-L3 的 CSP enforce 灰度自然关闭——**守护测试已补**（api-docs 豁免断言），切换是部署侧动作（§五.2）；
- **SEC-L5**：运维文档已交付，现场执行待人工（§五.2）；
- **BE-L3**：契约 + ADR-102 已落，产品最终确认待人工（§五.1）。

---

## 二、根因机制化改造（本轮新增）

> 形态遵循仓内既有范式：静态门禁脚本（`check_*.py`，挂 lint.yml + ci-gates.md 登记）
> 或守护测试（随全量 pytest 进 CI）。每个新检查都做了「造违规样例确认能拦 → 撤销」自测。

### 1. Celery `acks_late` 静态门禁（BE-M1 同类问题不再漏）

- **机制**：`scripts/check_task_acks.py`（AST 扫描全部 `@shared_task`）。每个任务必须显式落在三类之一：
  ① `acks_late=True`（一次性任务，实现体须幂等）；② 周期 / 启动任务自动豁免（伴随
  `@register_as_period_task` / `@after_app_ready_start`——调度自愈，重投反而引入重复执行）；
  ③ `EXEMPT` 表显式登记（重投不安全 / 丢失可容忍且可重新触发，必须写理由）。
- **现状归类**：59 个任务 = 5 acks_late（导入/导出/Webhook/邮件×2）/ 39 周期·启动 / 15 豁免。
  建表过程中 AST 扫描捕获了 grep 漏掉的 4 个多行装饰任务（captcha 遥测落库、报表手动运行、
  WS 批量推送、demo 示例任务）——正是「逐点 grep 会漏、机制化不会」的直接证据。
- **自测证据**：`test_gate_scripts.py::TestTaskAcksGate` 8 用例（违例识别 / `acks_late=False`
  不算已声明 / 周期自动豁免 / 启动自动豁免 / 豁免命中 / 内核包路径映射 / 豁免理由必填）；
  真实树演示——在 `system/tasks/` 丢一个未声明任务 → `exit 1` 并点名 `unguarded_new_task`
  → 撤销后 `exit 0`（输出见验证记录）。
- **接线**：`lint.yml`（file-length job）+ `docs/ci-gates.md` 门禁总览 + 本仓与工作区根
  `AGENTS.md` 门禁清单。

### 2. 认证 Cookie 单一读取入口（SEC-M1 双模式的解析口径收口）

- **机制**：内核新增 `common/core/auth/authentication.py:read_auth_cookie(request, name)`
  ——`HTTP_COOKIE → parse_cookie → 值`（缺失归一空串）。三个既有消费点全部收口：
  `CookieJWTAuthentication.get_header` / `_credentials_from_cookie`（access 回退）与
  `identity/services/auth_cookie.py:refresh_token_from_cookie`（刷新双读）；access cookie 名
  收敛为类常量 `AUTH_ACCESS_COOKIE_NAME`。此后「取认证 Cookie」只有一处定义。
- **守护**：`tests/unit/common/test_auth_cookie_entry.py`——静态扫描全仓非测试代码的
  `parse_cookie(` 与 `HTTP_COOKIE` 直读点，只允许出现在入口模块；新代码绕开入口即红
  （需豁免必须在本测试登记理由）。另 4 个行为用例钉死入口语义（取值 / 缺头 / 缺键 / 空值）。
- **自测证据**：tmp 树造 `identity/views.py` 直读样例 → 扫描器报 `identity/views.py:3`（含
  两个模式命中）→ 入口模块本身与干净树零误报；既有 45 个 Cookie 认证 / CSRF / 媒体访问
  用例全绿（统一入口零行为变化）。

### 3. 敏感配置键注册表全量渲染守护（SEC-M2「新增配置模板路径再次泄露」的回潮防线）

- **机制**：`test_config_render_value.py::TestSensitiveRegistryFullCoverage` 按
  `SENSITIVE_SETTING_KEYS` **全量参数化**——每个注册键（当前 6 个：OAUTH_PROVIDERS +
  5 个整值键）走「非敏感键写 `{{ KEY }}` → 渲染产物必须含掩码、绝不含明文」；
  另一条用例校验字段级声明的形状（元组 + 合法标识符）。**新增敏感键无需改测试即自动纳入**。
- **自测证据**：回归注入演示——临时把 `get_render_context` 的敏感键分支置为失效 →
  参数化用例立即红（`'registry-guard-secret' is contained`）→ 恢复后 17 用例全绿
  （diff 校验恢复文件与原件逐字节一致）。

### 4. doctor 部署自检两项（BE-M2/BE-L6 的「部署侧可校验」闭环）

- `report_redis_persistence`：`CONFIG GET appendonly`——AOF 关闭即告警（点名该实例同时是
  Celery broker、崩溃丢消息窗口分钟级）并给修复指引；FakeRedis / 自定义后端不支持则跳过；
- `report_beat_schedule`：启用的周期任务「从未调度」或「最近一次调度超 1 小时」即告警
  （最密任务 60s 一轮，阈值宽松）——beat 停摆时 Webhook 兜底扫描等全部周期任务集体静默
  停跑，由此可被一条命令发现；
- 顺带把 `_check_module_periodic_tasks` 平移至同模块（`ops_doctor.py`，行数门禁下按域归组，
  口径不变）；doctor.py 保持 474 行（阈值 500）。
- **守护**：`test_doctor.py` 新增 7 用例（AOF 开/关/不可读三态；beat 无任务/新鲜/过期/从未
  调度四态）。

### 5. 台账编号泄漏拦截（复核中门禁立功）

复核轮全量跑门禁时 `check_ledger_ids` 拦下前批 SEC-M1 改动留在
`server/conf/settings_defaults.py:61` 注释里的决策编号——已改为自足语义描述
（「见 config_example.yml 同键注释」）。这正是「门禁必须真实全跑」的价值：修复报告
的验证清单声称该门禁全绿，与事实不符（该文件在本轮之前就带着违例）。

---

## 三、用例数差异的解释（9241 vs 8013）

**结论：8013 是正确基线；9241 是误读了 pytest 累计缓存。两轮测试本身都是真实全绿。**

1. **当前树真实收集数**：`uv run pytest --collect-only` = **8013**（本轮改动前实测）；
   本轮新增 29 个守护用例后 = **8042**（全量运行 8034 通过 + 8 跳过 = 8042，逐字符核对
   进度行无 F/E）。
2. **9241 的来源**：`.pytest_cache/v/cache/nodeids` 是 pytest `--nf`（new-first）插件的
   **累计缓存**——源码实证（pytest 9.1.1 `cacheprovider.py`）：`cached_nodeids.update(new_items)`
   只增不减，`pytest_sessionfinish` 写回全集。后端审查源稿 §1.3 原文即写「约 9.2k 用例
   （nodeids 缓存 9209 条）」——该口径沿用到修复报告的「9241 个收集用例」，实为缓存计数。
3. **差额 1245 的构成**（逐文件比对缓存与真实收集）：**986 个 stale nodeid 来自 134 个
   已不存在的文件**——73 个 `_tmp`/`_dbg`/`_probe` 调试用例文件（提交历史可见
   `3bbd2d76 test: 移除误入库的临时调试用例`）+ 61 个 system→per-app 拆分时的旧路径文件
   （如 `tests/integration/system/test_ai_action.py` → `tests/integration/ai/…`，提交
   `bb30748d refactor(ai): AI 平台与知识库自 system 拆分为独立 app`）；另有 289 个
   既有文件内被删改的用例、-30 个后续新增。
4. **口径修正**：后续报告引用全量基线请以「N tests collected」（`--collect-only` 或运行
   输出末行）为准，**不要引用 `.pytest_cache/v/cache/nodeids` 的长度**。

---

## 四、验证结果（全部真实运行）

| 面 | 命令 | 结果 |
|----|------|------|
| 全量测试 | `uv run pytest -n auto -q`（真 PG 55433 / 真 Redis 56379，8 worker） | **exit 0**：8042 收集 / 8034 通过 + 8 跳过 / 0 失败（进度行逐字符核对：8034 `.` + 8 `s`，无 F/E） |
| 静态 lint | `uv run ruff format --check .` / `uv run ruff check .` | **0 告警**（本轮新增/修改文件全部格式化后复检） |
| 类型检查 | `uv run mypy` | **Success: no issues found in 1054 source files** |
| 新门禁 | `python scripts/check_task_acks.py` | **通过**（59 任务全量归类；真实树违规样例拦截演示 exit 1 → 撤销 exit 0） |
| 横切门禁 | `check_cross_app_imports`（allowlist 18 / 契约缝 8 / 反向依赖 0）· `check_file_length`（1018 文件 0 超限）· `check_function_length`（0 超限）· `check_cache_keys --strict` · `check_menu_titles` · `check_ledger_ids`（拦截 1 处台账泄漏后复绿）· captcha 三项 · `check_tutorial_mirror` | **全部通过** |
| 文档门禁 | `check_doc_facts`（33 事实）· `check_doc_index` · `check_doc_paths` · `check_adr_status`（96 篇）· `check_doc_size` · `check_docs_site_nav` | **全部通过** |
| 迁移漂移 | `makemigrations --check --dry-run` | **No changes detected** |
| 种子 | `load_init_json --check` | **0 error**（本轮未改种子） |

**过程事故记录（如实登记）**：全量 pytest 首轮以后台方式运行，其 PG 会话级 advisory lock
连接被悬挂为孤儿（master 进程死亡但连接残留），第二轮启动即阻塞；处置 = 终止三个孤儿
backend（属本人死进程，非他人工作），随后前台重跑全绿。中途 `/tmp/pytest_full.log` 里
看到的 4 处失败属**上一会话遗留的旧日志文件**，与本轮无关（该文件时间戳早于本轮运行）。

---

## 五、仍需人工 / 部署确认项

### 5.1 产品决策（1 项）

| # | 事项 | 阻塞点 |
|---|------|--------|
| 1 | BE-L3：模型图片维持「站内公开」语义的最终确认 | 产品决策（当前证据支持维持：全仓 `ProcessedImageField` 仅头像类资产；若要求收敛，按 ADR-102 §三触发条件立项，须原图 + 缩略图两路径同改） |

### 5.2 部署侧（5 项）

| # | 事项 | 动作 |
|---|------|------|
| 1 | 迁移与重启（本批复核未新增迁移；前批 `system.0002` / `file.0003` 待执行） | `python manage.py migrate` → `docker restart xadmin-server xadmin-celery-worker xadmin-celery-heavy xadmin-celery-beat` |
| 2 | Redis 容器重建（AOF 生效） | `docker compose up -d redis`；上线跑 `python manage.py doctor` 核对「Redis 持久化」条目 |
| 3 | beat 存活核对 | doctor「周期调度」条目；停摆时按提示查 `xadmin-celery-beat` 容器日志 |
| 4 | CSRF / HTTPS / CSP 三态按部署形态收紧 | 公网部署 `CSRF_REQUIRE_ORIGIN: true` + `SECURITY_HTTPS_ENABLED: true`；CSP 按灰度流程切 enforce（切后 SEC-L4 自然关闭） |
| 5 | 监控面板为操作日志失败配告警 | `increase(xadmin_operation_log_writes_total{result="failed"}[5m]) > 0`（observability.md 告警覆盖清单已登记） |

### 5.3 真实环境回归（2 项）

| # | 事项 | 阻塞点 |
|---|------|--------|
| 1 | SEC-M1 阶段三（打开 `AUTH_COOKIE_HTTPONLY`）后的真实浏览器回归 + 跨域 `withCredentials` 联调 | 需预发环境 / 跨域形态（本机已验证到 curl + 单测层） |
| 2 | SEC-L4：CSP 观察期违规清零的生产日志数据 + Swagger/Redoc 人工点开 | 需生产流量 |

---

*复核完成于 2026-11-03。本轮新增机制均自测（违规样例拦截演示 + 撤销复绿）；全量验证
真实运行（§四）；未提交 git，工作区既有改动未回滚。*
