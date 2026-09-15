# ADR-038：AI 助手受限动作（草稿 → 确认 → 以用户身份执行）

- 状态：已接受
- 日期：2026-09-15
- 关联：下一年度规划建议 §四.A2（W3–W4）；[ADR-023](ADR-023-ai-assistant-phase1.md)（AI 一期问答）；
  [ADR-026](ADR-026-dynamic-form-approval.md)（动态表单 × 敏感操作审批 412 协议）；
  [ADR-032](ADR-032-approval-business-integration.md)（审批接入业务系统）；
  `system/utils/ai_actions.py`、`message/ai.py`、`system/views/ai.py::action_execute`

## 背景

A2 的目标是把 AI 助手从「问答」深化到「权限内执行」：用户在聊天室用 `/do` 描述意图，
AI 产出受限动作草稿，用户在确认卡片上二次确认后才真正执行。规划同时划了三条红线：
AI 永不直接执行、不越权（以用户身份 + 权限双门）、必可审计（auth_type=ai）。

## 决策

### 1. 白名单动作注册表（唯一执行入口）

动作在 `ACTION_SPECS` 注册（首批两个：`leave.submit` 发起请假、`dform.submit` 提交动态表单），
每个动作声明：参数 schema（进 LLM prompt）、服务端校验函数、执行函数、所需底层业务权限点
（`(method, path)` 元组，与菜单权限点 path 同口径）、可用性与是否需要审批。
未登记的动作类型一律拒绝；LLM 输出按不可信输入处理（服务端逐项重校验，不信任前端回传）。

### 2. 交互形态：聊天 `/do` 命令 + 确认卡片

- `/do 请求描述` 与既有 `/kb` 同构（`message/ai.py`），REST 与 SSE 流式两条链路都支持
  （草稿为整段单 delta，无打字机语义）；
- 草稿落在 AI 消息的 `extra.action_draft`（持久化 + WS 广播 + 刷新可续聊），
  前端渲染确认卡片（动作名 / 摘要 / 参数行 / 需审批标记 / 确认与取消）；
- 信息不足时 LLM 返回澄清问题（`action: null`），按普通 AI 气泡渲染，不落草稿。

### 3. 执行：权限双门 + 412 审批协议复用 + 审计

`POST /api/system/ai/assistant/action/execute`（新权限点 `actionExecute:AiAssistant`）：

1. 灰度开关 `AI_ACTION_ENABLED`（默认关闭，AI 配置页可切换）+ AI 可用性门禁；
2. 白名单 → 可用性 → 权限双门（AI 端点权限 × 底层业务权限点，缺一不可）；
3. 参数重校验（与草稿生成同一校验函数）；
4. 动作声明需审批（如 `approval_required` 的动态表单、非超管）时复用 412 协议：
   建 PENDING 单（module 显式记为「AI 动作」）→ 412 + `approval_required` →
   审批通过后前端原样重发，http 拦截器自动携带 `X-Approval-Id`，一次性消费；
5. 以用户身份执行（creator/申请人恒为发起用户，参数中的替他人字段被忽略）；
6. 语义审计：`OperationLog(module=AI:action, auth_type=ai)`；
7. 执行结果回写 AI 房间（system 消息 + WS 广播，跨端可见、刷新可追溯）。

### 4. 灰度与边界

- 默认关闭（`AI_ACTION_ENABLED`，conf.py 默认 False + Setting 通路热切换）；
- 已知边界：卡片执行状态是前端本地态，刷新后回到 idle（房间内的结果回执消息
  保证了历史可读性）；`/do` 草稿质量依赖 LLM，参数与服务端校验兜底。

## 实施（2026-09-15 交付）

| 项 | 内容 |
|----|------|
| 后端 | 动作注册表 + 草稿 prompt（目录含动作 schema 与可用表单）+ parse 校验 + 执行器 + 审计；聊天 `/do` 双链路接入；execute 端点 + 状态接口扩展 |
| 前端 | 确认卡片（MessageBubble）+ execute API + AI 配置页灰度开关 + 词条 zh/en 对称 |
| 权限点 | `actionExecute:AiAssistant`（`api/system/ai/assistant/action/execute$` POST）入菜单种子，需重灌 `load_init_json` |
| 测试 | pytest 20 例（`tests/integration/system/test_ai_action.py`：白名单/参数/权限双门/审批协议闭环/跨用户令牌 403/替他人参数忽略/审计）；E2E 3 例 × 双浏览器（`e2e/ai-action.e2e.ts`，桩 LLM 全链路） |
| 桩 LLM | `scripts/stub_llm.py`（OpenAI 兼容最小实现）入 playwright webServer，AI 动作 E2E 依赖它 |

## 实施中发现并修复的既有缺陷（重要）

**AI 流式链路在真实浏览器中整体 406 不可用**：浏览器 `fetch` 携带 `Accept: text/event-stream`，
而 DRF 内容协商找不到该 media type 的渲染器（`APIClient` 默认 `Accept: */*` 会命中
JSONRenderer，pytest 全绿掩盖了问题）。修复：新增 `common/drf/renders/EventStreamRenderer`
并在 `ChatAiViewSet` 按 action 覆写 `get_renderers()`（注意 `@renderer_classes` 装饰器只对
`@api_view` 函数视图生效，ViewSet 必须覆写方法）；流式端点的门禁类 JSON 错误显式声明
`content_type=application/json`，保持「头前返回 JSON 1001」契约不变。回归测试以
`HTTP_ACCEPT=text/event-stream` 显式钉死（`test_chat_stream.py`）。

## E2E 工程教训（同步登记 e2e/README）

1. **共享库 + 同房间历史卡片**：按 `.last()` 取「最新卡片」会在新卡片尚未渲染时命中历史卡片
   （历史卡片同样可见可点，且其表单已被清理 → 执行必然失败）。卡片必须按本 run 唯一标识
   （随机表单名）`hasText` 定位；计数类断言用「执行前基线 +1」而非绝对 0。
2. **会话按用户隔离**：双浏览器先后运行共享同一库，跨用例用不同用户（各自独立 AI 房间）。
