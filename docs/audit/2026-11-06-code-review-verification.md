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
| A-B20-01 | 岗位成员候选接口未传 `user`，部门管理员可跨部门枚举用户 | **属实** | 已修：`identity/views/admin/post.py` user_options 补 `user=request.user`（与 `dept.py:110` 同口径）。**扩展核实**：`search_user_options` 共 7 个视图调用点，dataset/message 域 4 处同样未传——按既有口径处置（见 §三） |
| F11-P2 | 修改口令用用户名作 AES 密钥（B 篇 F12 回码确认为前后端一致弱密钥协议） | **属实，已修（v4 挑战协议）** | 见 §三「F11-P2 修复方案」 |
| F60-P2 | 文件分类饼图把后端未转义的字典 label 拼进 ECharts tooltip HTML（存储型注入面） | **属实** | 已修：`CategoryPieChart.vue` tooltip formatter 对 `p.name` 做 HTML 实体转义（局部 `escapeHtml`，与 codegen `ArtifactPreview` 同模式）。`UploadTrendChart` 复核确认只有日期/数值（安全，无需改）。影响面与 B 篇 F68 回码一致：管理员→管理员横向，非低权限提升 |

## 二、OPT 处置（逐条清单：表列 156 项 = A 篇 61 + B 篇 95）

> 口径：两篇自述合计 154 条有效 OPT（A 篇 61 / B 篇 93）。本清单按原文档表格逐行列出
> 156 项——F12-O1 为 F11-P2 的回码衍生观察、B 篇另有 1 条主审撤销的疑判未入最终表，
> 与「154 有效」的差即在此。
>
> 处置分布：**前轮已修 8** · **本轮 i18n 实修 9** · **本轮纵深实修 14** ·
> 审查期关闭 1 · **产品口径待定 19**（明细见 §五）· 不修 105（归并理由见 §二.4）。

### 1. 前轮已修（8 条）

| 编号 | 问题 | 修复 |
|------|------|------|
| A-B08-O1 | 同步流式非 200 分支未 `response.close()` | `integrations/sdk/ai/chat.py`：raise 前 `close`（getattr 防御测试桩） |
| A-B09-O1 | 敏感操作告警节流键全局 `method:path` 无用户维度，且投递失败后 60s 静默窗口 | `audit/notifications_alert.py`：节流键加 `creator_id` 维度；`publish` 失败回滚节流键（保持防并发 + 消除静默窗口） |
| A-B09-O2 | `record_original_channel_access` 的 path/method 未经列宽截断，超长即静默丢审计 | `audit/utils/mask.py`：path `[:400]`、method `[:8]` |
| A-B19-O2 | 审批提醒判重 `cache.get` + `cache.set` 非原子（并发双发窗口） | `approval/services/approval_flow/periodic.py`：改 `cache.add` 原子占位 + 失败回滚（保留原失败重试语义） |
| A-B21-O1 | 标签下拉缓存键含 limit，失效函数只删 limit=200 | `system/services/tags.py`：进程内登记实际使用过的 limit 集合，失效时并集删除（多 worker 由既有 60s TTL 兜底） |
| A-B23-O2 | modelset 列表缓存 `invalid_cache` 拼键删不到实际键 | **半属实**：`CacheDetailResponseMixin` 键与 `batch_invalid_cache`（identity/signal_handler.py:37-49）完全匹配、语义正确；`CacheListResponseMixin` 的 `invalid_cache` 确实删不到键，但唯一使用方 `UserSiteMessageViewSet` 从未调用它（无现行失效点，靠 TTL），且列表键含查询参数摘要、框架 cache API 不支持模式删除——根治需版本号键方案（收益低）。已在 docstring 加警示注释禁止新调用方依赖 |
| A-B05-O1 | MCP 端点审计 result 未脱敏 | `ai/views/mcp.py` 审计 result 补 `desensitize_payload`（随 A-B03-01 统一收口修复） |
| F59-O1 | 文件分类饼图 tooltip 拼 HTML + 上传缺 accept | tooltip 转义已随 F60-P2 同点修复（`CategoryPieChart.vue` escapeHtml）；accept 部分后端 `check_upload_limits` + MIME 白名单等价防护 |

### 2. 本轮实修（23 条：i18n 专项 9 + 防御纵深 14，每条带守护）

**i18n 专项（后端 3 + 前端 6）**——纯词条化 / 转义收口，不改外部行为：

| 编号 | 修复 | 守护 |
|------|------|------|
| A-B06-O1 | `ai/apps.py` verbose_name 包 `_()`；CLI stderr 文案按「CLI 不强制翻译」口径不译 | `test_i18n_po`（zh 译文 5 条补齐：无空译文 / 无 fuzzy / 无重复） |
| A-B15-O2 | 短信配置 `ALIBABA_ACCESS_KEY_ID/SECRET` 两个 label 包 `_()` | 同上 |
| A-B16-O2 | 验证码风险告警正文改 `_RISK_LABELS` 静态映射（复用存量译文避免重复 msgid）+ `format_html` / `format_html_join` 转义（消除 HTML 注入面） | 同上 |
| F02-O1 | 登录页版权文案词条化（`login.copyright`，年份插值不再写死 2026） | `check-i18n-keys`（zh/en 4974 条对称） |
| F03-O1 | 错误页诊断标签词条化（`error.diagDetailLabel` / `diagRequestIdLabel`；剪贴板机器格式保持不译） | 同上 |
| F04-O3 | OAuth 授权页用户名显示改插值词条（`oauthAuthorize.user`，标点随词条走）；「￥」等符号按不译口径 | 同上 |
| F10-O1 | 数据集表单操作符标签静态映射 `opLabels`（9 操作符）——不用动态拼键（动态键是 i18n 对称门禁的漏检面） | 同上 |
| F11-O1 | AccessToken 两处「中文逗号拼 `res.detail`」改 `results.failedWithDetail` 插值（复用既有词条） | 同上 |
| F15-O3 | 表单设计器控件类型标签静态映射 `columnTypeLabels`（5 类型） | 同上 |

**防御纵深（后端 10 + 前端 4）**——加校验 / 守卫 / 断言，不改外部行为：

| 编号 | 修复 | 守护 |
|------|------|------|
| A-B01-O3 | `register_action` 补静态声明冲突即时检查（注册时遍历 app_configs 查 `_app_declarations`，兑现 docstring 的 fail-fast 承诺） | `test_ai_registry_register.py`：冲突 fail-fast 用例 |
| A-B02-03 | `tracked_chat` / `tracked_chat_tools` 失败路径补 `invalidate_usage_cache(user)`（对齐成功 / 流式路径） | 同文件：mock 断言失效调用 |
| A-B05-O2 | 知识库 `batch_destroy` 元素类型归一（非 str/int 元素返回 1004 而非 500） | 同文件：畸形元素用例 |
| A-B11-O2 | codegen `_relative_path` 兜底分支返回 `generated/{name}` 单级名（防 zip-slip 条目名） | `test_defensive_path_guards.py`（22 个纵深守护用例之一） |
| A-B13-O1 | 上传校验改用 `sanitize_filename(file_obj.name)`，与落盘文件名同源 | 同上 |
| A-B17-O1 | 任务导出产物文件名 `sanitize_filename(record.name)` | 同上 |
| A-B18-O1 | 报表导出 / 邮件附件文件名 `sanitize_filename(report.name)` | 同上 |
| A-B24-O1 | `get_request_user` 匿名回落补 `logger.debug(..., exc_info=True)`（区分「令牌无效」与「解析服务异常」的可观测性） | 日志副作用不适用断言，靠 code review |
| A-B25-O1 | Flower 代理 path 白名单 `_ALLOWED_PATH_RE`（拒绝 `..` 段与注入字符） | `test_defensive_path_guards.py` 参数化用例 |
| A-B25-O2 | OpenAPI schema 缓存键加 `get_language()` 维度（语言间不再串缓存） | 同上 |
| F30-O1 | 路由编辑器 `ref([...props.routes])` 数组层拷贝（push/splice 不再就地污染父状态） | vue-tsc + 交互回归 |
| F33-O1 | 审批发起选人搜索接入 `createRequestSeqGuard` 序号守卫（项目既有范式，防竞态错序回显） | vue-tsc |
| F47-O1 | API 授权行过滤值行内形态校验 `matchValueError`（regex 编译探测 + ip_in 轻量形态；提示不阻断保存，后端 fail-closed 为准） | `check-i18n-keys` + vue-tsc |
| F64-O1 | 菜单树 ⌘K 聚焦搜索加 `inEditable` 判断（INPUT/TEXTAREA/SELECT/contentEditable 内不劫持组合键） | vue-tsc |

### 3. 审查期已关闭（1 条）

A-B01-O2：U-08 回码确认 `catalog(user)` 返回 forms 两键齐备，KeyError 前提不成立（文档保留痕迹，风险降级）。

### 4. 不修归并（105 条，按根因归类）

1. **后端有等价防护**（前端再加一道属纵深冗余，且多数会改变交互行为）：F01-O1、F04-O1/O2、F07-O1/O2、F08-O1、F11-O2、F13-O1、F15-O2、F16-O1、F17-O1、F18-O1、F19-O1、F22-O1、F23-O1、F28-O1、F32-O1、F35-O1、F48-O1、F50-O1、F62-O1、F63-O1、F67-O1、F70-O1、F71-O1、F75-O1——后端权限点 / serializer 校验 / MIME 白名单 / pinned_request SSRF 防护是权威收口。
2. **文本插值非 XSS**（B 篇自判口径，`{{ }}` / el-alert / ReReadonlyTable 均文本渲染）：F43-O1、F44-O1、F45-O1、F46-O1、F52-O1、F65-O1、F74-O1。
3. **后端权威文案 / 技术值 / 符号不译**：F01-O2、F08-O2、F13-O2、F14-O2、F16-O2、F17-O2、F18-O2（动态键已在 i18n 门禁登记面）、F25-O1（符号部分）、F39-O1、F57-O1、F58-O1、F66-O1、F69-O1、F72-O1、F73-O1——后端 detail 已按 locale 下发；「s」「—」「￥」全角标点与 Markdown 占位符是技术值，翻译反而破坏功能。
4. **降级 / 占位是有意设计**：F06-O2、F09-O1、F10-O2（badge 对 undefined 天然不渲染）、F31-O1、F55-O1（数据仅内存驻留不渲染）。
5. **注释漂移 / 展示一致性**（不影响运行）：A-B10-O2、A-B21-O3、F29-O1、F38-O1、F49-O1、F75-O1（树搜索大小写部分）。
6. **不可达分支 / 已有阻断**：A-B01-O4、A-B23-O1（compile_condition 先行拦截）、F34-O1（等价比较阻断回环）、F53-O1（plan-service 已有结构校验）。
7. **小概率异常路径 / TTL 自愈 / 有意取舍**：A-B02-05、A-B03-03/O4/O5、A-B04-O1（fail-safe 剥离方向正确）、A-B04-O2（desensitize_payload 按键名脱敏，字符串型 detail 无适用面且为排障所需）、A-B07-O2、A-B13-O2、A-B14-O1/O2、A-B15-O1、A-B16-O1（调用方三重校验）、A-B17-O2、A-B18-O2、A-B19-O1、A-B20-O1、A-B22-O1/O2（白名单键全部 `^…$` 精确锚定，本轮回码 custom.py 核实）、A-B08-O4、A-B01-O1（设计限定已声明）。
8. **行为变更需产品决策**：F02-O2/O3、F15-O1、F27-O1（近似匹配是产品妥协）、F40-O1（渲染侧净化是既定架构）、F42-O1、F51-O1、F54-O1、F56-O1、F61-O1、A-B10-O4。
9. **本地 CLI / 管理员工具 / 演示页**：A-B11-O1、F05-O1/O2。
10. **迁移纪律**：F76-O1（已发布初始迁移不改；PG17 必装 pg_trgm，触发面极小）。
11. **既有等价展示机制**：F36-O1（后端生成预览图）、F37-O1（SettingItem 掩码 + withoutMaskedSecrets 测试守护，B 篇 U-19 回码口径）、F59-O1（accept 部分）、F12-O1（页面级 auth 注入是组件编排模式）、F26-O1 保存链路。

### 5. 逐条判定清单

判定取值：已修（前轮）/ 已修（本轮 i18n）/ 已修（本轮纵深）/ 不修 / 待定（产品口径，明细见 §五）/ 关闭。

**A 篇（61 项）**

| 编号 | 判定 | 依据 |
|------|------|------|
| A-B01-O1 | 不修 | 行级复核「单一标量 pk」是 docstring 声明的设计限定；新增批量型动作时随声明扩展 |
| A-B01-O2 | 关闭 | U-08 回码：`catalog(user)` 返回 forms 两键齐备，KeyError 前提不成立 |
| A-B01-O3 | 已修（本轮纵深） | register_action 补静态声明冲突即时检查；`test_ai_registry_register.py` 守护 |
| A-B01-O4 | 不修 | 现状路由无 `/api/<app>/api/` 形态，域键误判无触发面 |
| A-B02-02 | 不修 | 告警本体仍在（U-07 核实）；节流键加来源域属可观测性增强 |
| A-B02-03 | 已修（本轮纵深） | tracked_chat/tools 失败路径补 invalidate_usage_cache；mock 断言守护 |
| A-B02-04 | 待定 | 跨轮注入防护属 AI 安全加固专项（历史隔离 / 扫描需能力取舍），另行排期 |
| A-B02-05 | 不修 | 配额 0 = 显式不限流是既定语义；心跳虚增仅影响观测值不改变准入 |
| A-B03-03 | 不修 | 向量容量上限的遗留约束不影响正确性；陈旧向量清理属运维任务 |
| A-B03-04 | 不修 | 切档案后 60s 查询缓存窗口自愈；同名模型异供应商属边缘配置 |
| A-B03-05 | 不修 | vector_index_state 聚合有 AUTO_INDEX_MAX_ROWS 上界，DDL 探测低频 |
| A-B04-O1 | 不修 | 未知键静默剥离是 fail-safe 正确方向（LLM 幻觉容错），调用方无需区分 |
| A-B04-O2 | 不修 | desensitize_payload 按键名递归脱敏，对字符串型 detail 无适用面；唯一含业务数据的 arguments 已脱敏，供应商异常文案正是排障所需 |
| A-B05-O1 | 已修（前轮） | `ai/views/mcp.py` 审计 result 补 desensitize_payload |
| A-B05-O2 | 已修（本轮纵深） | batch_destroy 元素类型归一返回 1004；畸形元素用例守护 |
| A-B06-O1 | 已修（本轮 i18n） | verbose_name 包 `_()`；CLI 文案按「CLI 不强制」口径不译 |
| A-B07-O1 | 待定 | 三处 OTP action 不走 UsedOtpCodeCache 属防重放口径分叉（confirm 有清绑缓存兜底、test 不改状态、open 幂等） |
| A-B07-O2 | 不修 | 二次解析开销小；两次解析间状态变化时下游取新值反而更安全 |
| A-B07-O3 | 待定 | TTL 配置热更语义（Redis TTL 与 time.time 判定不一致）需产品确认 |
| A-B07-O4 | 待定 | 恢复码加盐 / 慢哈希需兼容存量摘要的迁移方案 |
| A-B07-O5 | 待定 | 确认级别与 TTL 脱钩可能是有意设计（高级因子更可信） |
| A-B08-O1 | 已修（前轮） | raise 前显式 close |
| A-B08-O2 | 待定 | 7200s 是三家一致的保守假设；读 expires_in 需改三家 SDK |
| A-B08-O3 | 待定 | 短信供应商原始错误回显口径（与 IM 侧 ImSdkError 不一致） |
| A-B08-O4 | 不修 | tool_calls 分片受 STRUCTURED_MAX_TOKENS=2048 与 max_tokens 有界约束 |
| A-B09-O1 | 已修（前轮） | 节流键加 creator_id + publish 失败回滚 |
| A-B09-O2 | 已修（前轮） | path `[:400]` / method `[:8]` 截断 |
| A-B09-O3 | 待定 | remove_expired 是兼容 API（生产调用已收敛到归档链路），是否删除需运维口径 |
| A-B10-O1 | 待定 | 全员公告写动作的风险定级（是否 requires_approval） |
| A-B10-O2 | 不修 | exempt 理由文案漂移不影响 ai_tool_audit（已声明动作不进候选）；代码是事实源 |
| A-B10-O3 | 待定 | webhook 出站字段最小化（管理员配置的信任边界） |
| A-B10-O4 | 不修 | 部分成功可幂等重跑自愈；统一事务边界属行为变更 |
| A-B11-O1 | 不修 | 开发者本地 CLI，输入操作者自控，非运行时接口 |
| A-B11-O2 | 已修（本轮纵深） | _relative_path 兜底返回单级名防 zip-slip；`test_defensive_path_guards.py` 守护 |
| A-B13-O1 | 已修（本轮纵深） | 上传校验改用 sanitize_filename 与落盘同源；守护用例 |
| A-B13-O2 | 不修 | 审计旁路吞异常是有意取舍（不阻断业务），已有 warning 日志 |
| A-B14-O1 | 不修 | connect 拒绝后 return，当前无绕过路径；防御性重写属风格 |
| A-B14-O2 | 不修 | 撤回副本清理失败是异常路径小概率；主消息已撤回 |
| A-B15-O1 | 不修 | 静默降级属 dev-pitfalls 排障类已有 warning；行为变更需产品口径 |
| A-B15-O2 | 已修（本轮 i18n） | 两个 label 包 `_()` |
| A-B16-O1 | 不修 | 唯一调用方有开关 + 专用 token + compare_digest 三重校验，无绕过路径 |
| A-B16-O2 | 已修（本轮 i18n） | _RISK_LABELS 静态映射 + format_html 转义 |
| A-B17-O1 | 已修（本轮纵深） | 导出文件名 sanitize_filename；守护用例 |
| A-B17-O2 | 不修 | 注入 client 是测试可注入点（生产 _default_client 恒 None），与 ai/chat.py 既有模式同口径 |
| A-B18-O1 | 已修（本轮纵深） | 报表导出 / 附件文件名 sanitize_filename（499 行门禁内）；守护用例 |
| A-B18-O2 | 不修 | 调度派发有占位 / 释放与 TTL 自愈；统一 on_commit 属事务边界重构 |
| A-B19-O1 | 不修 | 指纹不符置 FAILED 是失败优先的安全方向；并发覆盖需双请求竞态且不越权 |
| A-B19-O2 | 已修（前轮） | cache.add 原子占位 + 失败回滚 |
| A-B20-O1 | 不修 | 历史令牌缺 scene 是有注释的升级兼容，按 TTL 自然过期 |
| A-B20-O2 | 待定 | SCIM all_objects 含软删行是否为目录同步语义（U-33） |
| A-B21-O1 | 已修（前轮） | limit 集合并集删除 |
| A-B21-O2 | 待定 | 权限钩子宿主覆写对账（U-33），是否由基类 / 门禁守护 |
| A-B21-O3 | 不修 | check_menu_titles 实际容忍（rstrip 句号 + 只判长度），风格漂移不影响运行 |
| A-B22-O1 | 不修 | 限流容错放行是有意设计（与 PatThrottle 同口径），注释已声明 |
| A-B22-O2 | 不修 | 白名单键全部 `^…$` 精确锚定，re.match 与 fullmatch 语义等价（本轮回码 custom.py:12-20 核实） |
| A-B23-O1 | 不修 | 编译链路 compile_condition 先行拦截，恒真分支不可达 |
| A-B23-O2 | 已修（前轮，半属实） | 警示注释 + 现状核实（唯一使用方从未调用） |
| A-B24-O1 | 已修（本轮纵深） | 匿名回落补 logger.debug(exc_info=True) |
| A-B25-O1 | 已修（本轮纵深） | Flower path 白名单 _ALLOWED_PATH_RE；参数化守护用例 |
| A-B25-O2 | 已修（本轮纵深） | schema 缓存键加 get_language() 维度；守护用例 |
| A-B26-O1 | 待定 | WS 中间件 fail-open 统一收口（架构约定） |

**B 篇（95 项）**

| 编号 | 判定 | 依据 |
|------|------|------|
| F01-O1 | 不修 | 后端构造 + HTTPS，B 篇自认无可利用路径；前端域白名单属纵深冗余 |
| F01-O2 | 不修 | MFA 方式名 / OAuth 供应商名是后端动态能力数据（按 locale 下发），前端词条化无意义 |
| F02-O1 | 已修（本轮 i18n） | 版权词条化 login.copyright（年份插值） |
| F02-O2 | 不修 | 隐私勾选手写判断改 :rules 会变更校验时机与样式，属交互行为变更 |
| F02-O3 | 不修 | 内联 style 字面量在样式门禁口径外（登录页既有模式） |
| F03-O1 | 已修（本轮 i18n） | 诊断标签词条化（机器格式保持不译） |
| F04-O1 | 不修 | redirect_uri 后端下发经 new URL 解析；与 F01-O1 同口径 |
| F04-O2 | 不修 | state 透传后端，OAuth state 校验以后端为权威 |
| F04-O3 | 已修（本轮 i18n） | oauthAuthorize.user 改插值词条；「￥」等符号按不译口径 |
| F05-O1 | 不修 | playground 演示页脚手架文案，非业务交付面 |
| F05-O2 | 不修 | 同上（演示页样式字面量） |
| F06-O1 | 待定 | ai_tokens_24h 契约字段是预留，日报卡是否补 token 维度属产品排期 |
| F06-O2 | 不修 | 欢迎页卡片降级语义（失败显示占位）是有意设计 |
| F07-O1 | 不修 | 上传有后端 MIME 白名单 + 大小校验（A 篇 B13 回码）；accept 属基础约束冗余 |
| F07-O2 | 不修 | 改名 / 成员 / 退群的后端权限点独立校验 |
| F08-O1 | 不修 | attachment.url 是后端生成的 media 路径（withApiDomain 归一），非用户可控外链 |
| F08-O2 | 不修 | aiHint 是后端动态文案（后端 i18n） |
| F09-O1 | 不修 | 取数异常降级到空态是有意设计 |
| F09-O2 | 不修 | 页面级 canEdit + 后端权限点；字面量在门禁口径外 |
| F10-O1 | 已修（本轮 i18n） | 操作符静态映射 opLabels（9 操作符，不用动态拼键） |
| F10-O2 | 不修 | 失败时 unread_count 为 undefined，badge 展示层天然不渲染 |
| F11-O1 | 已修（本轮 i18n） | 中文逗号拼接改 results.failedWithDetail 插值 |
| F11-O2 | 不修 | resetPassword 按钮显隐只是 UI 预览，端点权限点是权威 |
| F12-O1 | 不修 | F11-P2 回码衍生；页面级 auth 注入是项目组件编排模式 |
| F13-O1 | 不修 | 与 F01-O1 同口径（后端下发 URL） |
| F13-O2 | 不修 | 「s」单位与「—」占位符是符号字面量 |
| F14-O1 | 待定 | Passkey 注册 / 删除是否纳入 412 二次确认通道 |
| F14-O2 | 不修 | res.detail 单独渲染（后端已 i18n 的权威文案）；全角冒号是中文标点惯例 |
| F15-O1 | 不修 | 就地修改 props 引用在当前单向数据流下工作（父依赖同引用）；重构属行为变更 |
| F15-O2 | 不修 | 字典创建走后端独立权限点 |
| F15-O3 | 已修（本轮 i18n） | 控件类型静态映射 columnTypeLabels（5 类型） |
| F16-O1 | 不修 | 回滚执行端点后端权限校验权威 |
| F16-O2 | 不修 | 全角括号 / · / → 符号不译 |
| F17-O1 | 不修 | 动态表单 schema 校验在后端 serializer（权威） |
| F17-O2 | 不修 | placeholder 显示表单设计者原文是产品语义 |
| F18-O1 | 不修 | cron 解析以后端预览端点为权威 |
| F18-O2 | 不修 | componentLabel 动态键已在 i18n 门禁登记面（动态前缀 28 个）；符号不译 |
| F19-O1 | 不修 | max_rows 下推有后端 per-model 上限 |
| F19-O2 | 待定 | 大屏 html 组件是功能特性（CSP 面在 F24 链路评估） |
| F20-O1 | 不修 | 后端锁权威；按钮 disabled 属交互打磨 |
| F21-O1 | 不修 | 拖放落点判断是展示层细节 |
| F22-O1 | 不修 | 图片 URL 提示不阻断是设计；后端存储校验权威 |
| F23-O1 | 不修 | embed URL 后端 pinned_request 同源校验；提示不阻断是设计 |
| F24-O1 | 不修 | 自建白名单 + v-html 渲染侧收口（B 篇主审复核安全） |
| F25-O1 | 不修 | 与 F24 同链路已判安全；时钟占位符是符号 |
| F26-O1 | 待定 | 详情缺 is_owner 按可编辑放行的前端口径（保存后端校验） |
| F27-O1 | 不修 | 审批日志无外键关联，近似匹配是产品妥协；串台需同路径同主键同人碰撞 |
| F28-O1 | 不修 | 候选值校验后端 serializer 权威（allow-create 是选人交互特性） |
| F29-O1 | 不修 | 注释与实现漂移；自环路由在后端 flow 结构校验中拒绝（编辑器属展示层） |
| F30-O1 | 已修（本轮纵深） | ref 数组层拷贝防就地污染父状态 |
| F31-O1 | 不修 | 加载失败降级空态是有意设计 |
| F32-O1 | 不修 | target_order 后端 serializer 校验 |
| F33-O1 | 已修（本轮纵深） | searchUsers 接入 createRequestSeqGuard 序号守卫 |
| F34-O1 | 不修 | 回写循环已被等价比较阻断（JSON.stringify 等价即跳过），无实际回环 |
| F35-O1 | 不修 | path_patterns 后端 webhook 校验权威 |
| F36-O1 | 不修 | preview 响应是后端生成的预览图，非用户可控外链 |
| F37-O1 | 不修 | SettingItem 对 SECRET 类有掩码渲染（withoutMaskedSecrets 测试守护，U-19 口径）；风险规则按钮后端有权限点 |
| F38-O1 | 不修 | 判权与模板口径不一致是展示小瑕疵 |
| F39-O1 | 不修 | 未映射动作回退裸值是枚举兜底（技术值）；后端权限权威 |
| F40-O1 | 不修 | 富文本写入保原文是产品语义（编辑能力），净化在渲染侧收口 |
| F41-O1 | 待定 | raw 视图全字段展示是否脱敏 secret（管理员排障面） |
| F42-O1 | 不修 | 权限在会话期不变（登出整页重载），顶层常量取值安全 |
| F43-O1 | 不修 | AiMessageBlock 文本插值非 v-html |
| F44-O1 | 不修 | DSL 文本插值非 v-html |
| F45-O1 | 不修 | 调用日志 detail 文本插值 |
| F46-O1 | 不修 | ReReadonlyTable 文本渲染 |
| F47-O1 | 已修（本轮纵深） | matchValueError 行内形态校验（不阻断保存，后端 fail-closed 为准） |
| F48-O1 | 不修 | 后端上传 MIME / 扩展名白名单等价防护（A 篇 B13） |
| F49-O1 | 不修 | 注释与实现漂移；恒返回 {action, remark} 行为无害（弹窗流程由调用方控制） |
| F50-O1 | 不修 | 预览端点后端校验权威 |
| F51-O1 | 不修 | hasBatchSelection 字面量数组是项目标准用法 |
| F52-O1 | 不修 | el-alert title/description 是文本插值（Element Plus 不渲染 HTML） |
| F53-O1 | 不修 | plan-service 解析层已有结构校验（本轮回码核实） |
| F54-O1 | 不修 | assignReady 由父级提交按钮控制（组件契约） |
| F55-O1 | 不修 | plaintext 数组仅内存驻留不渲染；改响应结构需契约变更 |
| F56-O1 | 不修 | 预览组件展示后端原文是预览语义 |
| F57-O1 | 不修 | 后端 detail 权威文案 |
| F58-O1 | 不修 | 后端字段 + 权威文案 |
| F59-O1 | 已修（前轮覆盖） | tooltip 转义已随 F60-P2 修复；accept 部分后端等价防护 |
| F61-O1 | 不修 | 脱敏预览回显输入输出对照是功能语义 |
| F62-O1 | 不修 | 菜单 path 前导斜杠归一，后端 serializer 校验 |
| F63-O1 | 不修 | 权限点 path 由 sync_menu_permissions 种子对账收口 |
| F64-O1 | 已修（本轮纵深） | ⌘K 加 inEditable 判断（输入控件内不劫持） |
| F65-O1 | 不修 | 后端字段 prop 回显（文本插值） |
| F66-O1 | 不修 | 后端字段回显 + 单位符号 |
| F67-O1 | 不修 | 注释漂移；数值范围后端 serializer 校验 |
| F68-O1 | 待定 | 选人弹窗打开即拉用户列表（后端 list 权限点 + 数据权限收口） |
| F69-O1 | 不修 | Markdown 占位符（code / https://）是技术值，翻译反而破坏功能 |
| F70-O1 | 不修 | 批量端点后端权限点权威 |
| F71-O1 | 不修 | matchOptions / field_meta 是元数据协议模式（后端下发为权威） |
| F72-O1 | 不修 | 后端 note 权威文案 |
| F73-O1 | 不修 | 后端 detail 权威文案 |
| F74-O1 | 不修 | 登录日志字段后端原文回显（文本插值非 XSS，B 篇自判） |
| F75-O1 | 不修 | 试运行端点后端权限点（U-26）；树搜索大小写是展示一致性 |
| F76-O1 | 不修 | 已发布初始迁移不改（迁移纪律）；PG17 必装 pg_trgm 触发面极小 |

## 三、判定为不属实或需展开说明的 P2 级

- **A-B07-01 不属实**：见 §一（同域两视图口径自洽，改走数据权限反而破坏功能）。

### F11-P2 修复方案（v4 挑战协议，版本化密钥派生 + 存量兼容 + 可回滚）

改密口令传输加密从「可预测的 username 密钥」升级为「服务端下发的一次性随机挑战密钥」，对齐登录链路 verify_token 的既有模式：

- **协议格式**：密文以 `v4:` 前缀标记，payload 与 v2（WebCrypto PBKDF2-HMAC-SHA256 100k + AES-256-GCM）完全同构，仅密钥来源不同；`AESCipherV2.decrypt` 识别前缀选算法、密钥由调用方构造时传入（`common/base/crypto.py`）。`v3:` 前缀已被字段级加密占用，故顺延 v4。
- **挑战下发**：新增 `GET /api/identity/userinfo/password-challenge`（IsAuthenticated，本人）——`secrets.token_urlsafe(32)` 随机挑战，缓存 TTL 300s（覆盖「打开弹窗 → 提交」窗口），重复调用轮换旧值；权限点 `passwordChallenge:UserInfo` 登记种子（admin/default 角色授权与 resetPassword 同面，`sync_menu_permissions --dry-run` 缺口 0）。
- **解密**（`decrypt_submitted_password`）：`v4:` → 挑战密钥解密，挑战缺失/过期/认证失败一律拒绝且**不回退** username 密钥（防降级）；非 v4 → 原 username 协议（存量客户端兼容期）；`SECURITY_USER_PASSWORD_ENCRYPTED_ENABLED` 关闭时明文回退语义不变。
- **前端**（`useAccountManage.ts`）：拉挑战成功 → `AesEncryptedChallenge` 产出 v4 密文；挑战端点 404/失败 → 回退 username 协议。该「拉取时回退」天然覆盖全部部署组合：新后端+新前端=v4；旧后端（未升级或已回滚）+新前端=端点消失→自动回退；新后端+旧前端=username 密文兼容解。
- **守护测试**：后端 12 个新用例（挑战下发/轮换/隔离、v4 解密/防降级/挑战缺失拒绝、username 协议兼容、明文回退、API 鉴权、412 确认后 v4 全链改密）+ 前端 3 个（v4 前缀/随机性/异密钥防降级）。
- **剩余边界**：挑战 5 分钟窗口过期时用户会收到「解密失败请刷新重试」（重开弹窗自动轮换新挑战，低频可接受）；MFA confirm 等其余 3 处口令核验点仍用 username 协议（412 语境各有独立的暴力防护），如需统一升级可复用 v4 挑战机制另行排期。

### A-B20-01 扩展面处置（跨域功能选人显式豁免）

`search_user_options` 的 7 个视图调用点按**两种显式口径**收敛（`identity/utils/user_options.py` 模块 docstring 固化约定）：

1. **组织管理场景（传 user）**：`dept.py` / `post.py`——管理员管理组织成员，与列表页同口径套数据权限（post.py 本轮已补齐）；
2. **跨域功能选人场景（不传 user，显式豁免并登记）**：`dataset/views/report.py`（IM 收件人）、`form_data.py`（表单数据过滤）、`dform_submission.py`（填报选人/过滤）、`message/views.py`（群成员候选）——功能作者选择**目标用户**（选收件人/建群本就要能搜到全公司的人）。**不套数据权限的依据**：`get_filter_queryset` 无授权即 `none()`（fail-closed），未配置 `DataPermission` 授权的普通用户将得到空候选，功能被破坏。**固有约束**：认证 + 各功能 list 权限点 + 20 条上限 + 必须带 keyword/pks（无关键字不枚举），探测面收敛为「持功能权限者可按关键字搜索用户名/昵称」——这是功能选人的固有需求而非缺陷。4 处调用点 docstring 均已登记豁免理由；守护测试 `test_user_options_scope.py` 锁定双口径语义（传 user 无授权时空候选 / 不传 user 可搜全量）。不额外加限流：与 dept/post 等既有 user-options 端点口径一致（均无限流），report 域已有 `DatasetQueryThrottleMixin` 查询限流。

## 四、验证结果

| 项 | 命令 | 结果 |
|----|------|------|
| 后端定向单测 | `uv run pytest tests/unit/ai tests/unit/audit`（两轮） | 全绿（含新增 8 个守护用例） |
| 后端域回归 | `uv run pytest tests/unit/approval tests/unit/notifications tests/unit/identity tests/unit/integrations` | 全绿 |
| 后端集成 | `uv run pytest tests/integration/ai` | 0 失败 |
| **后端全量** | `uv run pytest -n auto` | **通过（EXIT=0）**。环境恢复后共三轮收口：首轮确认此前的「用户管理/角色管理」菜单顺序断言失败为**预存在 flaky**（fixture 子菜单未设 rank，同序下 DB 返回顺序不稳定；本轮给 fixture 补 rank 定序修复，连跑 5 次稳定）；次轮两个种子数量守护断言随新增 `passwordChallenge:UserInfo` 权限点同步更新（菜单 747→748 / 权限点 668→669 / default 角色授权 33→34）；末轮全绿 |
| OPT 轮定向复跑 | `pytest tests/unit/captcha tests/unit/common/test_i18n_po.py tests/unit/ai/test_ai_registry_register.py tests/unit/common/test_defensive_path_guards.py`；`pytest tests/unit/dataset` | 全绿（captcha 域覆盖 A-B16-O2；dataset 域 128 用例覆盖 A-B18-O1 及行数门禁回归） |
| 后端静态 | `ruff format --check` / `ruff check` / `mypy` | 全过（mypy 1054 文件 0 error；OPT 轮复跑并修复 `test_ai_registry_register.py` 的 import 排序 I001） |
| 后端本地门禁 | `check_cache_keys --strict`、`check_ledger_ids`、`check_menu_titles`、`check_file_length`、`check_function_length`、`check_cross_app_imports`、`check_doc_facts`、`check_doc_paths`、`check_adr_status`、`check_doc_index`、`check_doc_size`、`check_tutorial_mirror`、`check_task_acks`、`check_captcha_{coverage,legacy,assets}` | 全过（OPT 轮快扫复核：A-B18-O1 曾把 `dataset/analysis_tasks.py` 推到 502 行，import 块合并压回 499 行后门禁恢复） |
| 种子 | `load_init_json --check`（0 error）、真灌后 `sync_menu_permissions --dry-run`（缺口 0） | 通过 |
| 迁移漂移 | `makemigrations --check --dry-run` | No changes detected |
| 契约 | `docs/schema/openapi.json` 新增 password-challenge 端点 + 4 处 user-options description 同步豁免说明；前端镜像 + `openapi.d.ts` 经 `sync:contract` 同步；`check-contract-sync` 一致 | 通过 |
| 前端单测 | `pnpm test:run`（vitest） | 558 文件全过；OPT 轮全量复跑 **3399 用例全过**（含 v4 新增 3 个 + api-app 域 11 个定向） |
| 前端类型 | `tsc --noEmit` + `vue-tsc --noEmit` | 全过（OPT 轮含 F47 的 `matchValueError` 全量复跑） |
| 前端 lint | eslint（改动文件，--max-warnings 0） | 过（OPT 轮 10 个改动文件复跑，3 处 prettier 格式经 --fix 收敛） |
| 前端门禁 | template-root / font-size-literals / color-literals / size-literals / forms / tables / overlays / function-length / file-length / **check-i18n-keys** | 全过（OPT 轮新增词条后 zh/en **4974 条对称**，静态引用 3604 key 全命中） |

新增守护用例清单：`test_ai_action_audit_mask.py`（creator×3 + extra 脱敏）、`test_ai_api_params_stability.py`（缓存隔离×2）、`test_mcp_client.py`（字节口径）、`test_ai_async_chat.py`（非 200 资源释放）、`test_impact_api.py`（计算器异常 fail-closed）、`test_password_challenge.py`（v4 协议×10）、`test_user_options_scope.py`（双口径×4）、`test_aes_cipher_v2.py`（v4 格式×4）、`test_ai_registry_register.py`（注册冲突 fail-fast / batch_destroy 畸形元素 1004 / 用量缓存失效×3）、`test_defensive_path_guards.py`（Flower path 白名单参数化 / codegen 兜底 / swagger 语言维度 / sanitize_filename 路径剥离等 17 用例）、前端 `aes.spec.ts`（v4×3）。

## 五、剩余需人工确认项

1. **v4 协议的退役排期**：username 密钥协议（v1/v2）进入兼容期后，可参照既有 `SECURITY_AES_V1_DECRYPT_ENABLED` 灰度开关的模式，在确认全量客户端升级后考虑关闭兼容路径（当前保持双协议，无时间压力）。
2. **其余口令核验点是否统一 v4**：MFA confirm / OAuth 解绑 / verify-password 3 处仍用 username 协议（412 语境各有独立暴力防护），如需统一可复用挑战机制另行排期。
3. **产品口径待定 OPT（19 条，逐条判定见 §二.5 清单「待定」行）**，按主题分组：
   - **MFA / 认证策略（4）**：A-B07-O1（OTP 三处 action 防重放口径）、A-B07-O3（确认 TTL 配置热更语义）、A-B07-O4（恢复码加盐 / 慢哈希迁移）、A-B07-O5（确认级别与 TTL 脱钩）；
   - **供应商与通知（4）**：A-B08-O2（IM token TTL 读 expires_in）、A-B08-O3（短信供应商错误回显）、A-B10-O1（公告写动作审批定级）、A-B10-O3（webhook 出站字段最小化）；
   - **协议 / 架构约定（3）**：A-B20-O2（SCIM 软删语义，U-33）、A-B21-O2（权限钩子宿主覆写对账，U-33）、A-B26-O1（WS 中间件 fail-open 统一收口）；
   - **运维口径（1）**：A-B09-O3（两套日志清理是否收敛删除 remove_expired）；
   - **前端产品口径（6）**：F06-O1（日报卡消费 ai_tokens_24h）、F14-O1（Passkey 是否纳入 412）、F19-O2（大屏 html 组件保留面）、F26-O1（详情缺 is_owner 的可编辑口径）、F41-O1（raw 视图 secret 脱敏）、F68-O1（选人弹窗打开即拉列表）；
   - **AI 安全加固（1）**：A-B02-04（多轮历史跨轮注入防护，属能力取舍专项）。
4. **A 篇 9 个迁移文件补审结论**（F76）：仅 F76-O1 一条 OPT，已判定暂不修。

## 六、提交说明

两仓按主题提交（本地、未 push）；两份审查文档按其启动协议移入工作区 `_delete/2026-11-06-code-review-cleanup/`（临时产物、不入库）；本报告登记于 `docs/README.md` §四。

- **前两轮**（P2 修复 / v4 挑战协议 / A-B20 扩展面）：server 18 个主题提交 + client 2 个（F60-P2 tooltip 转义、F11-P2 v4 协议）。
- **本轮 OPT 批**：server 3 个（i18n 文案收口 / 防御纵深批 + 守护测试 / 本报告 OPT 清单）+ client 2 个（i18n 专项词条化 / 防御纵深批）。
