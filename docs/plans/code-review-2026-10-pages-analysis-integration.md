# 2026-10 全栈功能审查 · 分册：分析与集成（仪表盘/数据集/报表/大屏/AI/开放平台 9 页）

> 主册：[code-review-2026-10.md](code-review-2026-10.md)。证据等级：A=代码直接确证，B=跨文件静态推断，C=待运行时验证。
> Webhook 订阅/投递 2 页在 [pages-task.md](code-review-2026-10-pages-task.md)。
> 本域含 2026-10-25 快审（根目录 `code-review-2026-10-25.md`）三个高危项的**修复复核**，全部已在当前源码确认修复。

## 0. 修复复核（旧审查高危项，均 A 级证据）

| 旧编号(快审) | 内容 | 当前状态 |
|---|---|---|
| H-1 | 仪表盘联动 chip 与 WS 实时推送帧互斥缺失——推送帧把联动结果覆盖回全量 | **已修**：`ChartCard.vue:345-351` 联动生效期间丢弃推送帧并按当前 filters 重拉；`:352-353` 帧应用前作废在途 HTTP 序号（loadSeq++），早发晚到响应不再覆盖新帧 |
| H-2a | 数据集请求级 filters 不校验字段权限——隐藏列布尔预言机 | **已修**：`dataset/utils/dataset.py:188-215` `validate_filters` 接收 `user_obj`，与 `viewer_visible_fields` 求交，无权限字段报「No field permission」；并加 `FILTER_LIMIT` 数量上限（:205-206） |
| H-2b | filters 引用 columns 外的 JSON 路径 → FieldError 500 | **已修**：`build_queryset`（:331-357）把 condition_specs 与 ordering_spec 并入注解面，按需补 `json_<root>_<key>` 别名；docstring 明确记录该缺陷形态 |

## 1. 仪表盘（/analysis/dashboard/index）

- **标识**：dataset · analysis(可选) · `dashboard/index.vue` + ChartCard/useDashboardChannel 等 · 混合形态（布局元数据 + 卡片聚合）· 后端 `dataset/views/dashboard 相关`（dashboards CRUD + execute/aggregate 端点 + WS 推送 `ws/dashboard/`）。
- **前端实现**：卡片联动（点击维度值 → 目标卡带 filters 重拉）+ 实时推送通道（帧按卡片 id 路由 applyData，`useDashboardChannel.ts:21-32`）；两通道互斥已修（§0 H-1）。
- **后端实现**：推送载荷按卡片定义聚合（服务端聚合，观察者模式自推，beat 周期触发——git log e7041372）。
- **闭环校验**：权限 ✅（dashboards 端点权限点对账通过；WS 通道鉴权 C）；元数据 ➖（自定义画布）；契约 ✅（kind=execute/aggregate 帧语义统一）；数据 ✅（execute/aggregate 走数据集引擎的用户字段面求交，§0 H-2a）。
- **问题清单**：无。
- **E 建议（analysis-E-01）**：联动 chip 状态在推送帧丢弃重拉期间可加「轻量 loading 指示」，避免大卡重拉时视觉静止。价值：交互反馈完整性；成本 S；依赖无。证据等级 B（交互观感，属 Phase 2 复核范围）。

## 2. 数据集（/analysis/dataset/index）

- **标识**：dataset · analysis(可选) · `dashboard/dataset/index.vue` + hook（暂存改动中）· 元数据驱动 · 后端 `dataset/views/dataset.py DatasetViewSet`（206 行，execute/aggregate 请求级端点）。
- **后端实现**：执行链 = 白名单复核 → JSON 注解（含 filters/ordering 按需）→ CAS 无需（只读）→ 行级数据权限 fail-closed（`build_queryset`:369-372）；聚合侧 group_by/value_field fail-closed（快审确认，未回退）；NULLS LAST 跨库排序一致性（:364-367）；`viewer_visible_fields` 请求级 memo 缓存（:388）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅（业务码 1001 语义统一）；数据 ✅（字段面 + 行级双收敛）。
- **问题清单**：无。

## 3. 报表（/analysis/report/index）

- **标识**：dataset · analysis(可选) · 元数据驱动 · 后端 `dataset/views/report.py ReportViewSet`（BaseAnalysisViewSet + choices + run 动作）。
- **后端实现**：run 触发执行（同步聚合）；BaseAnalysisViewSet 可见性两档 + 创建者写保护（`analysis_common.py:19-84`，A）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 4. 大屏（/analysis/screen/index）

- **标识**：dataset · analysis(可选) · 元数据驱动 + 设计器 · 后端 `dataset/views/screen.py`（ScreenViewSet + command 动作 + ScreenTemplateViewSet 版本化/rollback + ScreenClipboardViewSet）。
- **后端实现**：模板版本快照/保留策略下沉 services（`analysis.py:10` 注释）；command 动作 GET+POST 同 handler（shared_method 权限口径，主册 §3.1）；投屏 WS 通道 `ws/screen/`。
- **闭环校验**：权限 ✅；元数据 ➖（设计器画布）；契约 ✅；数据 ✅。
- **问题清单**：无。
- **质量速记**：C：投屏页 WS 推送的鉴权与断线重连属运行时行为。

## 5. AI 助手（/integration/ai/index）

- **标识**：ai · ai(可选) · `integration/ai/index.vue`（334 行管理面）· 混合（对话入口 + 知识库联动）· 后端 `ai/views/assistant.py AiAssistantViewSet`（ask/ask-stream/nl-query/action/status/history/tools/usage/metrics/daily-report）。
- **后端实现**：权限点用正则交替收口 5 个只读动作（`ai/seeds/menu.json`：`status|metrics|history|tools|usage|daily-report` 一点），动作类端点各自独立点；`ai_chat_actions` 按用户限流（`assistant.py:131-139`）；SSE 流式协商走公共件。
- **NL 查数安全架构（横向确认）**：LLM 输出 → `parse_llm_json` → DSL 校验 → **复用数据集引擎** `build_queryset(dataset, user, extra_filters)`（`ai/services/nl_query/orchestrator.py:81-88`）——无裸 SQL、字段面与行级权限全继承；`visible_datasets(user)` 先收敛可查数据集（:64-66）。证据 A。
- **闭环校验**：权限 ✅；元数据 ➖（对话流）；契约 ✅；数据 ✅（NL 链路借道数据集引擎收敛）。
- **问题清单**：无。

## 6. AI 配置（/integration/ai/config）

- **标识**：ai · ai(可选) · 表单页 · 后端 `ai/views/assistant.py AiAssistantSettingViewSet`（BaseSettingViewSet 家族，aiApiKey 密文）。
- **闭环校验**：权限 ✅（list/partialUpdate/create:AiAssistantConfig 三点）；元数据 ✅；契约 ✅；数据 ✅（密钥 write_only 不回显，B）。
- **问题清单**：无。

## 7. 知识库（/integration/knowledge/index）

- **标识**：ai · ai(可选) · 薄壳 34 行 · 元数据驱动 · 后端 `ai/views/knowledge.py AiKnowledgeDocumentViewSet`（sync-repo/build-embeddings/batch-toggle/vector-status 等）。
- **后端实现**：向量化任务异步（build-embeddings + status 轮询）；sync-repo 拉取仓库文档；检索能力降级容错（迁移链首 pg_trgm/vector 失败仅告警）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅（C：分块隐私边界——知识库内容进入 LLM 上下文的可见范围=有 list 权限者，未配置更细粒度）。
- **问题清单**：无。

## 8. MCP 服务（/integration/ai/mcp）

- **标识**：ai · ai(可选) · 元数据驱动 · 后端 `ai/views/mcp_client.py McpServerViewSet`（CRUD + call + sync）+ `mcp_audit.py McpCallLogViewSet`（审计）。
- **后端实现**：call 为服务端出站请求——SSRF 面与 Webhook 同链（`common/utils/outbound.py` 公共件，B）；调用审计落 mcp-call-logs（含导出）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。
- **质量速记**：C：MCP server URL 的写入侧校验是否复用 `validate_outbound_config_url` 未逐行确认（出站公共件存在，风险面已被公共件覆盖的概率高）——记入未验证事项。

## 9. API 应用（/integration/api-app/index）

- **标识**：identity · open_platform(可选) · `api-app/index.vue`（137 行）· 元数据驱动 · 后端 `identity/views/open/open.py ApiApplicationViewSet`（grant-options/scope-options 白名单枚举 + 四级授权）。
- **后端实现**：应用四级授权（模型×动作×字段×行）在认证链统一收敛（`permission.py:381-409` `enforce_application_grant`，主册 §3.1 已核）；client_secret 凭证即身份的换发端点白名单化（settings/custom.py）。
- **闭环校验**：权限 ✅；元数据 ✅；契约 ✅；数据 ✅。
- **问题清单**：无。

## 批次 6 小结

- **发现数**：P0=0 / P1=0 / P2=0 / E=1（analysis-E-01，交互反馈增强，移交 Phase 2 复核）。
- **重大结论**：2026-10-25 快审的全部 3 个高危项（H-1/H-2a/H-2b）在当前源码已修复且有守护注释；NL 查数「LLM→受限 DSL→数据集引擎」架构无注入面。
- **未验证事项（移交主册 §7）**：WS screen/dashboard 通道鉴权运行时行为；MCP server URL 写入侧校验的公共件复用确认。
- **已读文件**：`ChartCard.vue`（330-365）、`useDashboardChannel.ts`（12-72）、`dataset/utils/dataset.py`（185-262, 331-405）、`dataset/views/{dataset(路由面),report,screen(结构),analysis,analysis_common}.py`、`ai/views/{assistant.py(108-190),nl_query(结构)}.py`、`ai/services/nl_query/orchestrator.py`（关键段）、`ai/seeds/menu.json`（权限点全表）；前端 dashboard/dataset 暂存改动文件（grep 交互段）。
