# ADR-082：六项休眠能力激活（开箱可用 / 演示态批次，TG-4 收口）

- 状态：已交付

> **日期**：2026-10-03
> **关联**：NEXT-DEV-PLAN §三.C（F7 原始登记）；触发制台账 TG-4；ADR-065/074（向量构建与 pgvector 链路）；ADR-023/024/038/048（AI 一~四期能力）；security-review 七期 S-2（ldap3 停维口径）
> **代码路径**：`server/conf/defaults.py`、`server/conf/settings_defaults.py`、`loadjson/systemconfig.json`（默认值转正）；`ai/utils/ai_embeddings.py`、`ai/utils/ai_knowledge.py`（自动重算调度与挂点）；`tests/`（守护）
> **背景**：TG-4 触发命中（2026-10-03 用户点名启动「开箱可用/演示态」批次）。六项能力代码齐备、默认休眠（F7-1~F7-6）；原始登记提示：转默认开启须同步补 ①向量自动重算（F7-6 即缺口本身）②渠道密钥管理，否则「能开但不好用」，按完整批次立项。

**对账记录（触发命中流程第一步）**：红线表逐项核对无触碰——AI 成本计价（ADR-076 触发制）不涉及：本批只动灰度准入开关与构建触发方式，用量账本 `record_usage` 既有口径不变，不引入定价/结算语义；多租户 / 移动端 / BPMN / 在线建表 / SSO / 多供应商 / common 包化 / pgbouncer 均无关。候选池无同域在办项。供应链 S-2 对 ldap3 的口径（「LDAP 功能默认关」）与 F7-3 决策一致并由此维持。

## 决策

- **D0：逐子项决策制**。原始登记按「六项转默认开启」的假设口径登记；落地判据是逐项核实「转开后开箱体验净增益且无半 broken 态」，而非把 False 改成 True。核实结论：三项转开（F7-1/F7-4/F7-5 部分键）、一项实现（F7-6）、两项维持关（F7-2/F7-3，配套②经核实无代码缺口，见 D4）。
- **D1（F7-1 转）：AI 四开关灰度转正 False→True**（`AI_ASSISTANT_ENABLED` / `AI_NL_QUERY_ENABLED` / `AI_ACTION_ENABLED` / `AI_NATIVE_TOOLS_ENABLED`）。安全性依据 = 四开关都不是「可用性开关」而是「灰度准入开关」，消费面在开关之外还有独立配置门控：助手 `is_enabled() = 开关 AND is_configured()`（激活档案或 Setting 凭据齐全，未配置部署行为零变化，前端 status 的 enabled/configured 锁定空态不变）；NL 查数开关 + `ai_enabled_check()` 双闸（数据权限 `visible_datasets` 口径不变）；受限动作 `ai_enabled_check()` + 「草稿→确认→以用户身份执行」fail-closed 协议（白名单/审计/配额不变）；原生工具开关 + 能力探测 `capability_ok` fail-closed（探测是管理端显式动作，无画像回落 prompt-JSON 轨道）。效果 = 「配置即亮」：装好 → 配档案 → 全链路可用，不再逐个开灰度开关。存量影响面：已配激活档案但开关从未动过的部署升级后 AI 面亮起（即本批产品意图）；显式关过的部署其 Setting 行（category=ai，AI 配置页可改）优先于代码默认，不受影响。灰度转正先例：SECURITY_PASSWORD_* 三项（2026-09-12 评审转正）。
- **D2（F7-4 转）：`METRICS_ENABLED` False→True**。开销核实：HTTP 采集为进程内计数器（`record_http_request` 无每请求 IO；redis 聚合只挂在 celery 任务信号路径）；端点三层门控原样（ENABLED → METRICS_TOKEN 非空 → Bearer 常量时间比较），无 token 部署端点 403、无暴露面。转开后「启用观测」只剩配置 token 一步（真开箱可用）。中间件挂载是启动期语义（非 SysConfig 热更），升级重启生效。
- **D3（F7-5 部分转）：保留期卫生默认值**。`CHAT_HISTORY_DAYS` 0→365（活协作数据取保守一年，与 `LOGIN_LOG_RETENTION_DAYS=365` 同档）；`FILE_KEEP_DAYS` 0→180（清理面只删「非临时、无业务引用」文件，与 `FILE_ACCESS_LOG_KEEP_DAYS=180` 同档）；`FILE_STORAGE_QUOTA_MB` / `FILE_UPLOAD_COUNT_LIMIT` **维持 0**——配额是分配策略不是卫生默认，非零默认会阻断上传（恰是「能开但不好用」）。清理任务已在位（`clean_chat_history_job` 03:23 / `auto_clean_upload_file_job` 02:56），本批只动默认值并同步种子 `systemconfig.json`（单源守护闭环）。存量部署其种子行（0）优先于代码默认，行为不变；全新安装获得保留期默认。**升级说明**：新装环境聊天历史保留 365 天、无引用上传文件保留 180 天，部署方可在系统配置页调整。
- **D4（F7-2/F7-3 维持关；配套②核实结论：管理面已齐备，无代码缺口）**：邮件/短信/钉钉/企微/飞书与 LDAP/SCIM/OAuth 维持默认关。判据：凭据是硬前置，转开无净增益且制造「开了但没配」的用户可见失败面（如 `EMAIL_ENABLED=True` 未配 SMTP 时验证码/找回密码链路报错——verify_code 配置回显把该开关直接下发前端展示入口）。渠道密钥管理配套逐面核实：邮件（message 页内嵌 tab）/ 短信（独立页）/ 企业 IM 三渠道（message 页三 tab）均 UI 可管（开关 + 凭据 write-only 值级加密落库 + 连通性测试动作），LDAP 有独立设置页，SCIM 令牌走凭据管理台原地轮换，OAuth 供应商走系统配置页（凭据台账 `change_entry` 已接线）。原登记「邮件/短信/IM 密钥目前手配」与代码不符（登记时未核实 UI 面），本批修正台账与 NEXT-DEV-PLAN §三.C 表述——「手配」的不可消除部分只剩「向供应商申请凭据」这一天然人工动作。
- **D5（F7-6 实现）：向量自动重算（本批核心工程交付）**。缺口（ADR-065 建立的显式语义）：正文变更后旧向量按 `embedding_hash` 判定陈旧、向量通道跳过该块，**无任何自动补齐**——AI 检索静默过期，依赖管理员记得点构建。设计：
  - 调度入口 `schedule_auto_rebuild(document=None)`（`ai/utils/ai_embeddings.py`）：embedding 档案未配置直接返回（向量链路未启用的部署零变化）；无待建块不调度（避免空转占锁）；构建单飞锁被占不排队（与手工构建互斥，防重复消耗供应商预算）；复用既有单飞锁 + `build_embeddings_task` 状态机（锁 → 任务 → 进度 → 终态 → 释放，与手工构建完全同一条链路）；eager（`.apply`）与 `transaction.on_commit(apply_async)` 双模与视图同口径。
  - 挂点三处（内容变更事实发生、文档行已落库之后）：上传/覆盖文档 `upsert_upload_document`（单文档调度）；停用→启用 `set_document_active`（停用期分块被清，启用重建后全为待建）；仓库同步 `sync_knowledge`（变更可能跨多文档，调度一次**全量增量**构建——一次任务吃掉全部陈旧块，不逐文档排队）。删除路径无需挂点（分块即删，SQL 检索天然反映）。
  - 语义边界：自动重算是**增量补齐**（force=False，只补缺失/陈旧块）；**模型换档不自动触发全量重嵌**（成本不可控，仍走构建按钮 force）——换档后的旧模型块由 `vector_index` 模型一致性判据跳过（词频通道兜底），自动调度只保证「正文新鲜度」不保证「模型一致性」。
  - 成本控制：增量口径 + 单飞锁互斥 + 无档案零触发；不新增配置开关——embedding 档案激活本身就是 opt-in，再加开关等于制造第七项休眠能力。
- **D6：守护测试**。自动重算（`tests/integration/ai/test_ai_embeddings_auto.py`）：有档案 + 上传新文档 → 分块自动获得向量（eager 同步）；正文变更 → 陈旧块自动补齐；无档案 → 零调度零异常；锁被占 → 跳过且不排队；未变内容重传 → 不调度；仓库同步变更 → 一次全量增量调度；模型换档 → 自动调度不重嵌旧模型块。默认值转正：开关默认值断言（防回退）。

## 验收

1. 新增守护测试全绿 + 既有向量/知识库/配置单源守护回归绿；
2. ruff / mypy / 全量 pytest（真实 PG17+Redis8）/ 行数 / 跨 app / 缓存键 / `makemigrations --check` / 文档四件套绿；
3. `test_config_defaults_single_source` 绿（settings_defaults 与 systemconfig.json 种子同步改）。

## 边界（登记）

- **不动**：通知渠道 `is_enable` 降级语义（开关 + 凭据齐全才可用）；AI 各能力内部协议（ADR-023/024/038/048）；`build_embeddings_task` 状态机与进度通道；检索链路 `retrieve` 契约。
- **不做**：渠道与企业集成开关转默认开（D4）；模型换档自动全量重嵌（D5 成本边界）；向量陈旧度常驻 UI（自动重算后「陈旧」是瞬态而非需要盯的状态，`vector-status` 端点既有 stale 计数保留）。
- **已知微变（接受并登记）**：① 未显式配置 AI 开关的存量部署升级后，已配置档案的 AI 面亮起（D1 产品意图，可经 AI 配置页关回）；② 全新安装获得保留期默认值（D3，可在系统配置页调整）；③ METRICS_ENABLED 转开后中间件挂载、端点由 404 变 403（无 token 时，D2）。

## 交付记录

- 2026-10-03：D0–D6 落地——AI 四开关与 METRICS_ENABLED 转正、保留期默认值 + 种子同步、`schedule_auto_rebuild` 调度入口与三挂点、守护测试；全量门禁见 NEXT-DEV-PLAN.md 执行记录十五。
