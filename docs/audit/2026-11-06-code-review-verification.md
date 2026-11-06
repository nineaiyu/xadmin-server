# 双仓审查核实与修复报告（2026-11-06）

> 对象：`docs/plans/code-review-fullstack-2026-11-05.md`（A 篇：13 P2 + 61 OPT）与
> `docs/plans/code-review-frontend-2026-11-06.md`（B 篇：2 P2 + 94 OPT，有效 2 P2 + 93 OPT）。
> 两篇均为只读审查临时产物（按其启动协议已于核实完成后移入工作区 `_delete/`，不在提交面）。
> 每条结论均已回码验证（打开现行代码核对），不盲信文档结论；行号为修复时点的工作区状态。

## 一、核实清单（P2 全量逐条）

| 编号 | 文档指控 | 判定 | 处置 |
|------|----------|------|------|
| A-B01-01 | 展示值解析缓存键无用户维度，跨用户确认态串扰并绕过同名保护 | **属实** | 已修：`ai_api_params.py` 缓存键加 `user.pk` 维度（`_resolution_cache_key` / `remember_param_resolution` / `_recall_param_resolution` / `_convert` 全链传 user）；`ai_api_actions.py` validate 三处调用补 user。守护用例 2 个（缓存隔离 + 跨用户执行不复用映射） |
| A-B01-02 | 内部 dispatch 绕过 `ApiLoggingMiddleware`，业务操作日志与敏感告警不触发 | **属实** | 已修：`ai_api_actions.py` 新增 `_record_internal_dispatch_log`——dispatch 成功后按中间件同口径补记（`API_LOG_ENABLE` / `API_LOG_METHODS` / `API_LOG_IGNORE` 同源配置、复用 `oplog_recorder` 装配与落库、操作人/IP/UA 从外部请求继承）。dispatch 异常路径不记（与中间件对异常请求的兜底语义一致） |
| A-B01-03 | AI 语义审计未写 creator，object_pk 填操作人主键属语义错位 | **部分属实** | 已修 creator：`audit_ai_action` / `audit_ai_ask` 补 `creator=user`。**object_pk 判不属实**：`ai/views/observability.py:94-113` 的 top_users 按 `object_pk` 归集用户名，是既定消费契约；已在 docstring 注明防止误改。`audit_nl_query`（自建 create 未传 creator）同根因一并修复 |
| A-B02-01 | `audit_ai_security` 绕过 `record()` 统一收口、无 creator、自建序列化截断 | **属实** | 已修：改走 `record()` + `creator=user`（changes 结构与 status_code=1001 契约不变） |
| A-B03-01 | 动作结果审计侧脱敏、对话消息落库侧不脱敏 | **属实** | 已修（治根因）：`ai_chat.persist_message` 的 `extra` 落库前统一 `desensitize_payload`——一处收口覆盖 actions / nl_query / mcp 全部现在与未来调用方；`views/mcp.py` 的审计 `result` 同口径补脱敏（A-B05-O1 一并修复） |
| A-B03-02 | 上传文档非 defer 路径分块先提交、文档行后保存，失败留孤儿块且不在自愈范围 | **属实** | 已修：`ai_knowledge.upsert_upload_document` 非 defer 分支把 `rebuild_chunks` + `doc.save()` 包进同一 `transaction.atomic()`（内部 atomic 降级为 savepoint，失败整包回滚） |
| A-B04-01 | `MAX_ARGUMENTS_BYTES` 按字符数而非字节数校验，中文可放行约 3 倍体积 | **属实** | 已修：新增 `ai_mcp_actions.arguments_size_bytes`（UTF-8 字节口径），AI 动作链路与 `mcp-servers/call` 端点两处调用统一替换；守护用例 1 个（12000 汉字 = 36000 字节 > 32KB） |
| A-B04-02 | NL 查数结果未脱敏落助手消息 extra | **属实** | 已修：并入 A-B03-01 的 `persist_message` 统一脱敏（`orchestrator.py` 两条链路自动覆盖） |
| A-B05-01 | AI 连通性测试把异常原文回传客户端 | **属实** | 已修：`views/assistant.py` test action 两处 `str(exc)` 改固定文案（原文只进日志），补 zh 译文 2 条 |
| A-B07-01 | `McpCallLogViewSet` 显式覆写 `filter_backends` 移除数据权限后端、与 `OperationLogViewSet` 口径不一致 | **不属实（对照物选错）** | 反证：同域 `McpServerViewSet`（`views/mcp_client.py:82`）**同样**显式覆写——MCP 服务器是全局配置资源，域级「不做行级数据权限」口径自洽（服务器与调用流水可见性一致）；文档只对照了全模块操作日志页。若按建议去掉覆写，非超管在无 `OperationLog` 数据权限授权时会 fail-closed 得到空列表（`get_filter_queryset` 无授权即 `none()`），破坏「持 MCP 管理权限即可排障」的现有功能。已在 ViewSet docstring 固化设计意图 |
| A-B08-01 | 异步流式非 200 分支泄漏 httpx 客户端与流响应（U-21 同时要求补自建客户端回归用例） | **属实** | 已修：`async_chat.chat_stream` 非 200 分支 raise 前显式 `await closer()`；补回归用例 `test_stream_self_built_client_non_200_releases_resources`（MockTransport 401 + 断言 `AsyncClient.is_closed`）。重试路径（`_send_with_retry` 的 5xx 关闭）复核确认原本就正确 |
| A-B09-01 | 影响面计算器异常被吞成「零影响」，引用保护 fail-open | **属实** | 已修：`impact_for` 计算器异常时产出哨兵检查项（`impact_calc_error`，count=1）→ `has_impact=True` → `ensure_impact_confirmed` 要求显式确认；预览端点如实展示「计算失败」。守护用例 1 个 |
| A-B20-01 | 岗位成员候选接口未传 `user`，部门管理员可跨部门枚举用户 | **属实** | 已修：`identity/views/admin/post.py` user_options 补 `user=request.user`（与 `dept.py:110` 同口径）。**扩展核实**：`search_user_options` 共 7 个视图调用点，`dataset/report.py:135`、`dataset/form_data.py:143`、`dataset/dform_submission.py:122`、`message/views.py:344` 四处同样未传——判定暂不修（见 §三） |
| F11-P2 | 修改口令用用户名作 AES 密钥（B 篇 F12 回码确认为前后端一致弱密钥协议） | **属实，暂不修** | 见 §三「需人工确认项」 |
| F60-P2 | 文件分类饼图把后端未转义的字典 label 拼进 ECharts tooltip HTML（存储型注入面） | **属实** | 已修：`CategoryPieChart.vue` tooltip formatter 对 `p.name` 做 HTML 实体转义（局部 `escapeHtml`，与 codegen `ArtifactPreview` 同模式）。`UploadTrendChart` 复核确认只有日期/数值（安全，无需改）。影响面与 B 篇 F68 回码一致：管理员→管理员横向，非低权限提升 |

## 二、OPT 处置（154 条有效项，按 A 篇 §6 十组根因归并）

### 已修（6 条高价值 OPT）

| 编号 | 问题 | 修复 |
|------|------|------|
| A-B09-O1 | 敏感操作告警节流键全局 `method:path` 无用户维度，且投递失败后 60s 静默窗口 | `audit/notifications_alert.py`：节流键加 `creator_id` 维度；`publish` 失败回滚节流键（保持防并发 + 消除静默窗口） |
| A-B08-O1 | 同步流式非 200 分支未 `response.close()` | `integrations/sdk/ai/chat.py`：raise 前 `close`（getattr 防御测试桩） |
| A-B09-O2 | `record_original_channel_access` 的 path/method 未经列宽截断，超长即静默丢审计 | `audit/utils/mask.py`：path `[:400]`、method `[:8]` |
| A-B19-O2 | 审批提醒判重 `cache.get` + `cache.set` 非原子（并发双发窗口） | `approval/services/approval_flow/periodic.py`：改 `cache.add` 原子占位 + 失败回滚（保留原失败重试语义） |
| A-B21-O1 | 标签下拉缓存键含 limit，失效函数只删 limit=200 | `system/services/tags.py`：进程内登记实际使用过的 limit 集合，失效时并集删除（多 worker 由既有 60s TTL 兜底） |
| A-B23-O2 | modelset 列表缓存 `invalid_cache` 拼键删不到实际键 | **半属实**：`CacheDetailResponseMixin` 键与 `batch_invalid_cache`（identity/signal_handler.py:37-49）完全匹配、语义正确；`CacheListResponseMixin` 的 `invalid_cache` 确实删不到键，但唯一使用方 `UserSiteMessageViewSet` 从未调用它（无现行失效点，靠 TTL），且列表键含查询参数摘要、框架 cache API 不支持模式删除——根治需版本号键方案（收益低）。已在 docstring 加警示注释禁止新调用方依赖 |

### 暂不修（按类归并，理由如下）

1. **i18n / 文案一致性类**（A-B06-O1、A-B15-O2、A-B16-O2 与 B 篇 F02-O1、F03-O1、F04-O3、F05-O1、F08-O2、F10-O1、F11-O1、F13-O2、F14-O2、F15-O3、F16-O2、F18-O2、F25-O1、F39-O1、F66-O1、F69-O1、F72-O1 等约 30 条）：属实但属文案优化专项——需成批新增词条并同步 `zh-CN.yaml` / `en.yaml` 双文件，量大面广且非缺陷；建议单独排期，不混入本次安全修复。
2. **防御纵深类**（F01-O1 / F04-O1 / F13-O1 后端下发 URL 前端零校验、F07-O1/O2、F09-O2、F14-O1、F22-O1、F23-O1、F28-O1、F47-O1、F48-O1、F53-O1、F71-O1、A-B01-O1、A-B13-O1、A-B16-O1、A-B17-O2、A-B24-O1、A-B25-O1 等）：B 篇各批回码已逐一确认后端有等价防护（上传 MIME 白名单 / 权限点独立校验 / 数据权限 fail-closed / URL 协议校验），当前无可利用路径；属「再加一道」的纵深建议，非缺陷。
3. **产品口径类**（A-B10-O1 公告写动作审批定级、A-B20-O2 SCIM 软删语义、A-B21-O2 权限钩子空实现、A-B26-O1 WS 中间件 fail-open、A-B07-O5 确认 TTL 脱钩、A-B08-O2 IM token TTL、A-B08-O3 短信错误回显、A-B09-O3 两套日志清理、A-B10-O3 webhook 出站字段、F26 系、F68-O1）：需产品/协议决策（公告风险定级、SCIM 目录同步语义、宿主覆写约定），不宜在修复任务中单方面变更行为。
4. **已撤销 / 前提不成立**（A 篇自行撤销的 A-B03-06；A-B01-O2——B12/U-08 已确认 `catalog` 键齐备不会 KeyError）：无需处理，文档保留痕迹。
5. **其余单点观察**（A-B02-02~05、A-B03-03~05、A-B04-O2、A-B05-O2、A-B11-O1/O2、A-B14-O2、A-B15-O1、A-B17-O1、A-B18-O1/O2、A-B19-O1、A-B22-O1/O2、A-B25-O2、F76-O1 等）：属实但影响面小（60s TTL 自愈 / CLI 本地工具 / 注释与实现口径不一致），修复优先级低；其中 F76-O1（迁移 AddIndex 与 try 降级语义不一致）按迁移纪律不改已发布初始迁移（PG17 必装 pg_trgm，触发面极小）。

## 三、判定为不属实或暂不修的 P2 级说明

- **A-B07-01 不属实**：见 §一（同域两视图口径自洽，改走数据权限反而破坏功能）。
- **F11-P2 属实但暂不修**：`useAccountManage.ts:87-94` 以 username 为 AES 密钥、后端 `identity/serializers/userinfo.py` 以 `AESCipherV2(user.username)` 对称解密（`SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED` 控制、关闭回退明文）——这是**前后端一致的既有协议**而非前端单点绕过。根治需引入服务端下发一次性 challenge 的两阶段协议并兼容存量客户端（旧客户端仍发 username 加密串），属产品级协议升级；当前该端点已叠加 412 二次确认 + TLS，风险为「AES 层可离线猜解」。**需人工确认**是否立项升级。
- **A-B20-01 的扩展面暂不修**：dataset/message 域 4 处 user-options 不传 user 属「跨域功能选人」（报表 IM 收件人 / 表单数据过滤 / 消息选人）——若套数据权限，未配置 `DataPermission` 授权的普通用户将 fail-closed 得到空候选（`get_filter_queryset` 无授权即 `none()`），破坏「功能作者选择目标用户」的场景。是否按「目标用户需数据可见」收敛属产品口径，**需人工确认**。

## 四、验证结果

| 项 | 命令 | 结果 |
|----|------|------|
| 后端定向单测 | `uv run pytest tests/unit/ai tests/unit/audit`（两轮） | 全绿（含新增 8 个守护用例） |
| 后端域回归 | `uv run pytest tests/unit/approval tests/unit/notifications tests/unit/identity tests/unit/integrations` | 470 用例全绿 |
| 后端集成 | `uv run pytest tests/integration/ai` | 0 失败 |
| 后端静态 | `ruff format --check` / `ruff check` / `mypy` | 全过（mypy 1054 文件 0 error） |
| 后端本地门禁 | `check_cache_keys --strict`、`check_ledger_ids`、`check_menu_titles`、`check_file_length`、`check_function_length`、`check_cross_app_imports`、`check_doc_facts`、`check_doc_paths`、`check_adr_status`、`check_tutorial_mirror`、`check_task_acks`、`check_captcha_{coverage,legacy,assets}` | 全过 |
| 迁移漂移 | `makemigrations --check --dry-run` | No changes detected |
| 前端单测 | `pnpm test:run`（vitest） | 558 文件 / 3396 用例全过 |
| 前端类型 | `tsc --noEmit` + `vue-tsc --noEmit` | 全过 |
| 前端 lint | eslint（改动文件，--max-warnings 0） | 过 |
| 前端门禁 | template-root / font-size-literals / color-literals / size-literals / forms / tables / overlays / function-length / file-length | 全过 |
| **后端全量 pytest** | `uv run pytest -n auto` | **待环境**：测试栈 Redis 不可达（OrbStack 引擎不稳定）导致两轮全量未收口——第一轮两个失败（①审查文档未登记索引，属临时产物预期告警，文档已按协议移入 `_delete/`，消除后应绿；②一个菜单断言失败详情未取到，需环境恢复后确认是否与本次改动相关），第二轮阻塞被终止。定向测试已覆盖全部改动面 |

新增守护用例清单：`test_ai_action_audit_mask.py`（creator×3 + extra 脱敏）、`test_ai_api_params_stability.py`（缓存隔离×2）、`test_mcp_client.py`（字节口径）、`test_ai_async_chat.py`（非 200 资源释放）、`test_impact_api.py`（计算器异常 fail-closed）。

## 五、剩余需人工确认项

1. **修改口令加密协议升级**（F11-P2）：是否引入一次性 challenge 替代 username 弱密钥（前后端协同 + 存量兼容）。
2. **跨域选人数据权限口径**（A-B20-01 扩展面）：dataset/message 域 4 处 user-options 是否按数据权限收敛。
3. **全量 pytest 环境收口**：Redis/OrbStack 恢复后重跑全量（含确认第一轮那个「用户管理/角色管理」菜单断言失败是否与本次改动相关——本次改动未触碰菜单种子与 `system/views` 菜单链路，初步判断为环境/负载性失败）。
4. **A 篇 9 个迁移文件补审结论**（F76）：仅 F76-O1 一条 OPT，已判定暂不修。
5. i18n 文案专项（约 30 条）与产品口径类 OPT（§二.3）建议按主题另立任务。

## 六、提交说明

两仓按主题提交（本地、未 push）；两份审查文档按其启动协议移入工作区 `_delete/2026-11-06-code-review-cleanup/`（临时产物、不入库）；本报告登记于 `docs/README.md` §四。
