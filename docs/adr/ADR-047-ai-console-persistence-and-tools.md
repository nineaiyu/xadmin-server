# ADR-047：AI 助手对话页改版（分栏导航 + 对话持久化 + 统一工具目录）

- 状态：已接受
- 日期：2026-09-20
- 关联：[ADR-038](ADR-038-ai-assistant-restricted-actions.md)（受限动作与白名单注册表）；
  [ADR-023](ADR-023-ai-assistant-phase1.md)（AI 一期问答）；
  `ai/models/ai.py::AiChatMessage`、`ai/utils/ai_chat.py`、`ai/views/`、
  `xadmin-client/src/views/integration/ai/`

## 背景

助手页一期是「单卡 + 页签」形态（文档问答 / NL 查数两个页签），存在三个问题：

1. 对话不持久化——刷新即丢，操作记录只在 OperationLog 审计里，无法回看上下文；
2. 「指令执行」只在聊天室 `/do` 命令里可用，助手页没有入口；
3. 思考面板两处体验缺陷：内部 200px 滚动区不自动跟随增量（长思考停在最前）、
   思考面板标题与正文等待区同时显示两处「思考中…」。

## 决策

### 1. 左右分栏布局（对齐聊天室）

助手页改为微信式两栏：左栏 = 功能导航（文档问答 / 数据查询 / 指令执行，按权限点
`ask / interpret / actionExecute:AiAssistant` 组装，无权限入口不出现）；右栏 =
该入口的消息流 + 输入区（时间分组 / 向上翻页 / 流式气泡 / 停止生成，复用聊天室
交互口径）。窄屏（<768px）左栏折叠为抽屉。

### 2. 对话持久化（AiChatMessage）

- 新表 `system.AiChatMessage`：`(creator, feature)` 组织消息流，自增主键即游标
  （`before_id` 倒序翻页）；`role = user/assistant/system`；`reasoning` 截断落库
  （与聊天室同口径 4000 字）；`extra` 承载 sources / nl / nl_run / action_draft /
  action_result / partial / error（DjangoJSONEncoder 兜底 UUID/datetime）；
- 落库收口在 `ai/utils/ai_chat.py`：各流式端点在「校验通过」后落 user 消息
  （头前错误不落库），done/error 帧携带服务端持久化载荷（`message` 键）——
  前端乐观上屏按载荷对齐，刷新后从 `GET history` 得到同一份数据；
- `history` 并入 `status:AiAssistant` 权限点路径正则（GET 组：
  `(status|metrics|history|tools)$`），无新增权限点。

### 3. 指令执行入口（草稿流式端点）

`POST /api/system/ai/assistant/action/interpret/stream`（SSE：meta → reasoning\* →
delta\* → done | error），复用 ADR-038 的注册表与 `parse_draft` 服务端校验；
done 载荷 `{kind: "draft"|"message", draft?, message}`——draft 渲染确认卡片，
确认后走既有 `action/execute`（白名单 + 重校验 + 权限双门 + 412 审批协议 + 审计）。
助手页来源（未携带 `room_id`）的草稿与执行结果落 `AiChatMessage`；聊天室来源仍由
ChatMessage 承载，不重复落库。执行类操作（NL run / 动作执行）的结果作为独立消息
上屏，确认/运行按钮仅对「最新一条 assistant 消息」渲染（执行后新消息追加，
卡片自然变为只读回看，与刷新后的历史渲染口径一致）。

### 4. 统一工具目录（MCP tools/list 等价）

`GET /api/system/ai/assistant/tools` 输出当前用户**有权执行**的全部动作：
`{name, description, inputSchema}`（JSON Schema，`const` 固定参数不出现）。
这是系统对外的统一功能接口：LLM function calling、外部 MCP 客户端、二开脚本
共用同一份目录与执行链路（`execute_action`）。新增能力 = 注册表加一条声明
（优先 `ai_api_actions.api_action` 声明式复用既有业务接口）。

### 5. 声明式动作扩展（覆盖高频管理操作）

新增五条声明（零 AI 专用业务代码，全部复用现有 ViewSet 的权限链/校验/审计）：

| 动作 | 业务接口 | 说明 |
| --- | --- | --- |
| `user.search` | GET /api/system/user | 读类动作（IN_QUERY）：按用户名/昵称/启用态查询 |
| `notice.list` | GET /api/notifications/notice-messages | 读类动作：查看系统公告（notice_type 服务端固定） |
| `user.update` | PATCH /api/system/user/\<pk\> | 更新昵称/手机号/邮箱（仅提供的字段） |
| `role.create` | POST /api/system/role | 新增角色（用户组），fields/menu 契约由 defaults 满足 |
| `role.grant` | PATCH /api/system/role/\<pk\> | 配置菜单权限：菜单名解析为「菜单 + 子树全量权限点」 |

配套机制扩展（`ai_api_actions.py`）：

- `IN_QUERY` 参数归属（GET 动作参数走 query string，resolve 只接受纯 path）；
- `role` / `menu` 参数类型（名称/主键宽容解析；菜单名 → 子树 pk 展开，与授权树
  勾选父节点同语义；确认卡片回显角色名/菜单名，回传解析幂等）；
- `target` 键（参数名对 LLM 友好如 `menus`，落请求体改写为接口契约字段 `menu`）；
- 分页响应（`{total, results}` 无 ApiResponse 包装）在 `execute_api_action`
  中按成功处理（读类动作命中列表接口）。

### 6. 思考面板体验修复

- `AiThinking` 内部滚动跟随：增量到达时贴底跟随（用户上翻回看不打扰，
  滚回底部恢复），折叠展开时补滚到底部；
- `AiMessageBlock` 去重：思考面板自带「思考中…」标题时，不再渲染底部等待动画
  （仅模型不产思考时，等待动画作为唯一活动指示出现）。

## 安全与边界

- AI 永不直接执行、以用户身份执行、权限双门、fail-closed 审计等红线全部沿用
  ADR-038；读类动作同样受 `user_can_visit` 双门 + 视图数据权限约束
  （查询结果随调用者权限收窄）；
- 对话消息按 creator 隔离，`history` 不接受用户参数；
- 角色/权限类动作（`role.grant`）替换式授权且确认卡片明示语义，普通用户还受
  字段级权限裁剪（业务接口既有约束）。

## 后续

- MCP server 转译层（把 `tools` 目录暴露为标准 MCP endpoint）登记候选池；
- 动作目录随业务需求滚动扩声明（每条一个 API 声明，无业务代码）。
