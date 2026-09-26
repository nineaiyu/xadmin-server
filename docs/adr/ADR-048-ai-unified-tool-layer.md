# ADR-048：AI 统一工具层——全系统动作覆盖 + MCP 协议端点 + 多步串联

- 日期：2026-09-21
- 状态：**已交付**
- 背景：AI 受限动作机制（ADR-047 / A2）已具备「注册表 → 工具目录 → 执行收口」的统一功能接口骨架，但注册表只登记了 7 个声明式动作 + 2 个内置动作，远不能覆盖系统可执行操作（用户/组织/权限/公告/监控/数据分析/任务/配置/日志/审批）；外部 MCP 客户端（Claude / Cursor / Cherry Studio 等）无法接入（`tools` 端点只是 HTTP/JSON 形态的 tools/list 等价物）；且 LLM 一次只能产出单个动作草稿，「新增用户组 → 配权限」这类多步操作需多轮对话。

## 决策

### D1 注册表全量扩容（57 个声明式动作 + 3 个内置动作）

- 新增 ~45 个声明式动作，全部复用既有业务接口（零 AI 专用业务代码），按域拆分声明文件（500 行门禁）：
  - `ai_api_registry.py`：组织/公告域（user.create/detail/unblock、online、dept、role、menu、permission、approval 读写、site-messages）+ 三域汇总；
  - `ai_registry_ops.py`：监控 7 个只读端点、数据集 list/meta/execute/aggregate、定时任务 list/registered/enable/run/executions/log；
  - `ai_registry_infra.py`：系统配置、数据字典、操作/登录日志、文件、导出、知识库、AI 自观测（ai.metrics）、Webhook。
- 新增内置动作 `dashboard.overview`：内部 dispatch 6 个首页统计端点合并返回（一次调用拿到全貌，避免模型连发 6 个细粒度查询），`required_visits` 列全 6 个权限点。
- **明确不声明的能力**（写进注册表注释的红线）：
  - `user.destroy` / `user.batch_destroy` / `user.reset_mfa`：业务端挂密码二次确认（`UserConfirmation.require(ConfirmType.PASSWORD)`），AI 链路不能安全携带密码（密码不得进对话/审计/审批快照）；`user.reset_password` 因接口要求前端 AES 加密协议同理排除；
  - 批量类（batch-*）动作一律不声明：AI 单条操作语义即可；
  - `approval.cancel`：无 AI 化收益。
- 防拼错守护测试（`test_ai_api_registry_guard.py`）：遍历全部声明校验 key 格式/参数规则/占位符对应关系，且**每个 path 填样例主键后必须 `resolve()` 可解析**——声明式动作拼错 path 的失败模式是静默降级（执行端返回「动作不可用」、权限预检永远 False），守护测试让拼错变红。

### D2 高危动作：AI 层强制审批 + 审批穿透

- 高危动作（role.delete / dept.delete / approval.approve / approval.reject / config.set / user.reset_password 类语义）声明 `requires_approval=True`，复用既有 412 审批协议（审批单 module=「AI 动作」，申请人不能自审）。
- **超管豁免**（`requires_approval_high_risk`，与 dform 动作同口径）：审批协议要求申请人不能自审，超管通常是唯一管理员——不豁免会因审批人缺失/自审禁止而永久挂起。
- **审批穿透**：AI 层审批单建在 `action/execute` 路径上；审批通过、令牌消费后内部 dispatch 到业务端点时，若该路径同时被 `APPROVAL_REQUIRED_PATHS` 配置命中，业务层 `@ApprovalRequired` 会再建第二张审批单（该单无法从 AI 链路消费，死循环）。修法：`execute_api_action` 对 requires_approval 动作在内部请求上设 `request._approval_pre_authorized = True`，`process_approval` 首行检测该标记直接放行。该标记仅服务端内部构造的请求可设置，外部 HTTP 请求无法注入，不存在绕过面。
- 语义：AI 动作层已完成同一操作指纹的强制审批，内层 dispatch 不再重复拦截——一次操作、一张审批单。

### D3 MCP 协议端点（Streamable HTTP，无状态）

- `POST /api/system/ai/mcp`（`ai/views/mcp.py`）：JSON-RPC 2.0 单对象，方法 `initialize` / `ping` / `tools/list` / `tools/call`；通知（无 id）返回 202 空体；不返回 `Mcp-Session-Id`（无状态模式，协议允许）；batch 请求返回 -32600。
- `tools/list` 与助手页 `tools` 端点同源（`tool_catalog`），另附 `annotations.readOnlyHint`（全 GET 动作）与 `_meta.x-requires-approval`；`tools/call` 走 `execute_action` 唯一收口 + `audit_ai_action` 审计（changes 含 `channel: "mcp"`）。
- **高危动作在 MCP 通道一律拒绝**（isError + 引导走 Web 控制台）：MCP 无 412 审批协议（一次性令牌重放由 Web 前端拦截器驱动），机器通道不给破坏性操作开后门。
- 认证走既有 DRF 认证链：外部客户端用 **PAT**（`Authorization: Pat <token>`）或 JWT，以令牌属主身份执行，权限双门与 Web 控制台同口径；PAT 可用 scope/IP 白名单进一步收敛。
- 权限点 `mcp:AiMcp` 登记 loadjson/menu.json + menumeta.json（fail-closed）。

### D4 多动作草稿串联（1~3 个/请求）

- `build_draft_prompt` 允许 LLM 一次产出最多 3 个动作草稿（按执行顺序），输出 `{"actions": [...]}`（`ALLOWED_ACTIONS_JSON:` 标记格式不动，E2E 桩依赖）；`parse_draft` 兼容旧单对象契约（存量桩/E2E/二开脚本不受影响），逐项服务端校验（白名单/可用性/权限双门/参数），超限拒绝，多草稿错误带 `#序号` 前缀。
- 契约：`parse_draft` 返回值恒含 `drafts`（数组）与 `draft`（首个，兼容旧渲染）；SSE done 载荷与消息持久化 extra 同口径（`action_drafts` / `action_draft`）。
- 前端：`AiActionCard` 增加 `testidPrefix` prop（默认 `ai`），聊天室 `MessageBubble` 重构为复用该组件（传 `chat` 前缀，既有 E2E 选择器 `chat-action-*` 不变，消除两份卡片实现）；渲染层按数组循环出多张卡片，每张独立确认/执行/412 重发（不做「全部执行」——逐项确认是安全红线的一部分）。

## 影响与验证

- 工具目录从 9 → 60 个动作；`GET /api/system/ai/assistant/tools` 与 MCP `tools/list` 自动同步，无需前端改动。
- 测试：守护测试 7 例、扩展集成 18 例（只读/写入/聚合/高危 412 全链路/审批穿透/多草稿契约）、MCP 协议 12 例；存量 AI/审批/聊天回归全绿。
- 翻译：新增 ~182 条 zh msgstr（紧邻既有 AI 块手工追加，未跑 makemessages 避免 location churn）。
- 后续演进：动作继续扩容只需加声明（含拆分文件）；MCP 端点如需流式（SSE）响应可在现有 JSON-RPC 分发上加 transport 分支；多步串联的「全部执行」暂不做，待真实使用反馈再评估。
