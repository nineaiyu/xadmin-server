# ADR-034：聊天室重构（微信式两栏 + 私聊 + AI 助手）

- 状态：已接受（2026-09-14 实现落地）
- 关联：
  [ADR-003](ADR-003-websocket-protocol.md)（WS 协议契约三处同步）、
  [ADR-023](ADR-023-ai-assistant-phase1.md)（AI 一期 RAG）、
  [ADR-033](ADR-033-knowledge-base-management.md)（知识库，`/kb` 命令的数据源）；
  规划文档 `docs/plans/聊天室重构计划-2026.09.md`（已交付，2026-09-14 清理，去向登记见 [plans/README.md](../plans/README.md)）；
  `message/models.py`、`message/chat.py`、`message/consumers.py`、`message/views.py`、`message/ai.py`

## 背景

聊天室是框架最早的页面之一，功能只有「单房间文本行 + 回车发送」，与当前基建严重脱节：

- 前端 `views/chat/index.vue`（172 行）消息无气泡/头像/时间分组，`search/filteredItems` 是死代码，刷新即丢；
- 后端 `message` app 无模型、无 REST，消息零落库，靠 WS 广播到硬编码组 `message_system_default_0`；
- 「进聊天室」这条路由靠**隐式规则**判定：URL 第二段 `username != 登录用户名`；
- 已有基建未被使用：`UserSession`/`/api/system/online`（在线）、`ChatCompletionsClient` + 知识库（AI）、
  `push_message` 通知管道（@提及）；
- @提及只认行首第一个词，无多目标。

## 决策

### 1. 数据模型：三张表，room_key 幂等

```
ChatRoom（BigAuto pk）
  ├─ room_type: public（全站单例）/ private（一对一）/ ai（每用户一间）
  ├─ room_key: public | dm:{min_pk}:{max_pk} | ai:{owner_pk}   ← UniqueConstraint 保证幂等
  ├─ owner（仅 ai 会话）、last_message / last_message_time（会话列表零成本排序）
ChatRoomMember（仅 private/ai 维护行）
  └─ last_read_id（已读游标=消息 id）+ unread_count（冗余计数，避免每次 count）
ChatMessage（BigAuto pk = 自增游标）
  ├─ sender 可空（AI/系统消息）、sender_name 快照（改名不回溯历史）
  ├─ message_type: text | ai | system；content ≤2000（AI/系统消息 ≤8000）
  ├─ client_msg_id：`(sender, client_msg_id)` 部分唯一索引（断线重发去重）
  ├─ is_recalled / recalled_time（本人 2 分钟内）、extra（AI 引用来源等）
  └─ Index(room, -id) 支撑 before_id 游标分页
```

公共聊天室**不建成员行、不做未读**（进入即浏览）：未读语义只对「点对点」会话成立。

### 2. WS 通道：新增 `ws/chat/`（ChatNotify），旧通道完全不动

旧 `ws/message/<group>/<username>`（MessageNotify）继续承担全站通知推送 + 登录日志 + 会话登记；
新聊天页走 `ws/chat/`：

- **不登记 UserSession / 不写登录日志**：会话登记由全局通知连接唯一负责，
  在线列表（`/api/system/online`、`get_online_info`）不会把同一用户数成两条；
- 组名显式：公共广播组 `chat_room_public` + 用户聊天组 `chat_user_{pk}`（多端同步），
  **不再依赖 URL 参数隐式判定**；
- 组名刻意避开 `websocket_group_` 前缀，`RedisChannelLayer.user_pk_from_group` 返回 None，
  心跳只续期聊天组、不写在线索引（守护测试 `test_ping_keeps_chat_groups_without_online_index`）；
- 准入 fail-closed：登录 + 持有聊天室会话列表权限（`list:ChatRoom`）才 `accept`，否则 4403。

协议扩展（三处同步：`message/protocol.py` ↔ `docs/schema/ws-frame.schema.json` ↔ 前端 `protocol.ts`）：

| action | 方向 | 载荷 |
|---|---|---|
| `chat_message` | ↑ 发送 | `{room_id, content, client_msg_id}` |
| `chat_message` | ↓ 广播 | `{id, room_id, room_type, sender_pk, sender_name, sender_avatar, message_type, content, created_time, client_msg_id, extra}` |
| `chat_recall` | ↑/↓ | `{message_id}` → `{message_id, id, room_id, operator_pk}` |
| `chat_read` | ↑ | `{room_id, last_read_id?}` → 回执最新游标 |
| `chat_unread` | ↓ | `{room_id, unread_count}`（私聊/AI 未读红点） |

`chat_message` 这个 action 名在两条通道上载荷不同：新通道用 `ChatRoomMessagePayload`
（落库记录形态），历史通道保留 `ChatMessagePayload`（text/pk/username），已在 protocol.py 文档化。

### 3. 分发拓扑与提醒

- 公共消息 → 落库 → `chat_room_public` 一次广播；
- 私聊/AI → 落库 → 成员各自 `chat_user_{pk}`（多端同步）+ 接收者 `chat_unread`；
- 私聊站内信提醒（`message_type=chat_private`）：**对端已开聊天页时跳过**（消息已实时送达，
  不重复弹窗），未开页面且 `PUSH_CHAT_MESSAGE` 偏好开启才推 `websocket_group_{pk}`；
- @提及（公共房间）：全位置、多目标解析（`@([\w.\-]+)` 去重保序），跳过自己，
  受 `PUSH_CHAT_MESSAGE` 约束，复用既有 `push_message` 管道（`message_type=chat_message`）。

### 4. REST（`/api/chat/`，无尾斜杠，与 system/notifications 同口径）

| 接口 | 说明 |
|---|---|
| `GET /api/chat/room` | 我的会话列表：公共置顶 + AI 助手（门禁开启时）+ 私聊（未读优先） |
| `POST /api/chat/room/open-private` | `{user_pk}` → `get_or_create` 私聊（双端幂等） |
| `GET /api/chat/message?room=&before_id=&limit=` | 倒序游标拉取（默认 20 / 上限 50），响应内按时间正序 + `has_more` + `can_recall` |
| `POST /api/chat/message/{id}/recall` | 撤回：仅本人、2 分钟内，成功后向房间广播 `chat_recall` |
| `GET /api/chat/contacts` | 最近在线联系人（UserSession 最近活跃 + 在线态） |
| `POST /api/chat/ai/message` | AI 提问：多轮上下文；`/kb` 前缀走知识库 RAG |

权限点 6 个（种子 `menu.json`/`menumeta.json`，父菜单 = 聊天室页面）：
`list:ChatRoom` / `create:ChatRoom` / `list:ChatMessage` / `recall:ChatMessage` /
`list:ChatContact` / `ask:ChatRoom`，并同步授予内置 4 个角色（管理员/默认权限/演示模式/test）。

> 偏离说明：规划文档写「AI 接口复用 `ask:AiAssistant` 权限点」，但 `Menu.name` 存在
> 「未删除唯一」约束，同一权限码不能挂两个 URL，故新增 `ask:ChatRoom`（同一套 AI 门禁语义）。

### 5. AI 双形态

- **通用多轮**：人设 + 本会话最近 20 条（user/assistant 交替，系统消息与撤回消息跳过，
  去掉本轮提问避免重复一轮）+ 本轮提问 → `ChatCompletionsClient.chat(messages)`；
- **`/kb 问题`**：复用 `system.utils.ai.ask`（词频检索 + 引用），回复的 `extra.sources`
  携带出处供前端折叠展示；`/kb` 后无问题给出可读提示；
- **门禁**：`AI_ASSISTANT_ENABLED` + 凭据齐全（`system.utils.ai.is_enabled`），
  未启用时左列表不显 AI 会话、接口返回可读 1001；
- **降级**：LLM 失败/知识库无命中 → 落一条 `system` 消息（`extra.error=true`，前端可见）
  并同时返回 1001 可读 detail，不静默；
- AI 走 REST 同步（LLM 5~60s 不占用 WS 长连接）；流式 SSE 留二期。

### 6. 前端形态

```
src/views/chat/
  index.vue              # 两栏骨架 + 响应式（<768px 侧栏折叠为抽屉）
  components/ChatSidebar.vue   # 搜索 + 固定会话（AI/公共）+ 最近在线联系人
  components/ChatWindow.vue    # 头部 + 消息区（时间分组/向上加载/新消息悬浮条）+ 输入区
  components/MessageBubble.vue # 气泡：头像/昵称/时间/撤回/系统消息/AI 引用来源
  hooks/useChat.ts             # WS 订阅、消息状态、游标分页、发送幂等、@联想
  hooks/useRooms.ts            # 会话列表、未读红点、open-private、联系人
src/api/chat/index.ts          # REST 封装
src/utils/websocket.ts         # 新增 ChatWebSocket 子类（/ws/chat/），协议类型扩展
```

- 聊天页自建 `ws/chat/` 连接，不与 user store 的全局连接争抢 `onmessage`；
- 未读红点：WS `chat_unread` 实时更新 + 连接建立即下发快照（首屏对齐）；
- `@` 触发在线成员联想（`@username ` 插入），Enter 发送 / Shift+Enter 换行；
- 消息**一律文本插值渲染**（禁 `v-html`），长度服务端强制。

## 后果

- 消息落库，刷新/重进历史仍在；私聊、未读、撤回、@多目标、AI 会话全部可用；
- 旧的 `MessageNotify` 聊天分支保留但已无页面使用（纯兼容），可在后续版本评估删除；
- 新增两张业务表 + 一张成员表；公共房间消息无上限增长（历史清理任务登记二期候选
  `CHAT_HISTORY_DAYS`）；
- 明确不做（二期候选）：图片/文件消息、多人群聊、已读回执展示、全局消息搜索、
  表情包、AI 流式输出（SSE）、桌面 Notification API、消息全文检索。

## 实现落地记录（2026-09-14）

- `message/models.py` + 迁移 `0001_initial`：`ChatRoom` / `ChatRoomMember` / `ChatMessage`；
- `message/chat.py`：会话开通（public/private/ai 幂等）、消息落库（client_msg_id 幂等）、
  未读游标、撤回、会话列表、最近联系人、@提及解析；
- `message/utils.py`：`chat_room_public` / `chat_user_{pk}` 组名、`room_event_groups`
  （房间→目标组唯一口径）、`push_room_event`（REST 同步广播）；
- `message/consumers.py`：`ChatNotify`（准入/入组/发送/撤回/已读/未读快照）；
  `message/routing.py` 增 `ws/chat/`；
- `message/views.py` / `serializers.py` / `urls.py`：6 个 REST 接口 + `server/urls.py` 注册；
- `message/ai.py`：多轮上下文裁剪 + `/kb` RAG 分流 + 门禁与降级；
- 菜单/权限种子：6 个权限点 + 4 个内置角色授权；po 词条 zh/en（44 条）；
- 测试：`tests/unit/message/test_chat_models.py`（25）、
  `tests/integration/message/test_chat_api.py`（28）、
  `tests/integration/message/test_chat_consumer.py`（15）、
  `tests/unit/message/test_protocol.py` 扩展（含 schema 同步守护）。
- E2E：`e2e/chat.e2e.ts`（两栏布局 + 公共聊天室收发与刷新持久化、私聊实时送达 + 未读红点 +
  已读清零）+ `notice-push.e2e.ts` 改写走新页面（第二个浏览器上下文发 @提及，断言全局通道收到
  `push_message`，并断言公共房间实时广播可达）。

## 二期（2026-09-14）：表情包 / AI 流式（SSE）/ 历史自动清理 / 桌面通知

### 1. AI 流式输出（SSE）

- 端点：`POST /api/chat/ai/stream`（`text/event-stream`），复用 `ask:ChatRoom` 的门禁语义
  但单独登记权限点 **`stream:ChatRoom`**（`api/chat/ai/stream$`，POST）——DRF 权限链按
  请求路径匹配菜单 path 正则，`api/chat/ai/message$` 覆盖不到新端点，不补点即全员 403；
- 事件序：`meta`（问题正式载荷）→ `delta`*（文本增量）→ `done`（AI 消息正式载荷）| `error`
  （可读 detail + system 降级消息载荷）。流式响应头发出后无法再改状态码，失败一律带内下发光；
  门禁/参数/房间错误仍在响应头之前返回 JSON `code=1001`，前端按普通接口错误提示；
- SDK：`ChatCompletionsClient.chat_stream`（OpenAI `stream=true` SSE 兼容，逐段产出增量，
  错误口径与 `chat()` 一致）；`/kb` 问答整段一次产出（知识库检索本身非流式，不做假打字机）；
- 落库/广播与 `ai/message` 同口径：流结束才写 AI 消息行并广播（多端经 client_msg_id 对齐）；
  全程无增量即失败 → system 降级消息 + error 事件；已有增量后中断 → 保留部分回答
  （`extra.partial` 记录中断原因）+ done 事件；
- 响应头带 `X-Accel-Buffering: no`（防 nginx 攒满 buffer 才转发）与 `Cache-Control: no-cache`；
  请求中间件已核安全：操作日志/计时中间件对 `.data/.content` 全部走 getattr 防护；
- 前端：axios 不支持流式，`src/utils/sse.ts` 用 fetch + ReadableStream 手工解析
  （解析器 `parseSseBuffer` 为纯函数，vitest 钉死半帧/多帧/CRLF/多行 data 场景）；
  气泡内「▍」光标闪烁，切会话/卸载自动 abort；登录态用 `formatToken(getToken())` 手工携带。

### 2. 历史自动清理（`CHAT_HISTORY_DAYS`）

- 配置位：`common/core/config.py::CHAT_HISTORY_DAYS`（默认 **0 = 不清理**）+
  `loadjson/systemconfig.json` 登记（系统设置界面可改）；
- 任务：`message/tasks.py::clean_chat_history_job`（每日 03:23，`register_as_period_task`，
  celery autodiscover 自动注册，无需 system/tasks.py 显式引入）；清理实现在
  `message/chat.py::clean_expired_history`：按 id 升序小批（2000）删除，只删消息行、
  不动会话与成员关系（历史清空的会话仍在列表，摘要自然为空）；
- 测试：`tests/unit/message/test_chat_cleanup.py`（删旧留新 / 0 = no-op / 配置读取 /
  任务可运行），配置断言沿用 patch SysConfig property 的范式。

### 3. 表情包与桌面通知（前端）

- 表情包：内置 64 个常用表情（不引第三方依赖），`el-popover` 网格 + 光标处插入
  （textarea `selectionStart/setSelectionRange`），面板 `data-testid="chat-emoji-panel"`；
- 桌面通知：`src/utils/desktopNotify.ts`（Notification API），开关持久化 localStorage
  （聊天室头部铃铛，开启时按需申请权限）。口径钉死在 `shouldNotifyDesktop`：
  **聊天类推送（@提及/私聊）前台也弹；其余站内推送仅 `document.hidden` 时弹**
  （前台有应用内通知，避免双重打扰）。点击聚焦窗口并复用各分支的路由跳转；
  正文一律 `stripHtml` 纯文本（推送正文可能带 HTML）。E2E 通过 `addInitScript`
  注入假 Notification（permission=granted + 实例记录）断言，绕开真实权限弹窗。

## 验收（已执行 2026-09-14）

- 后端：`pytest tests` **2087 passed**（新增 25 模型单测 + 28 REST 集成 + 15 WS consumer +
  3 权限种子守护 + 协议/schema 同步守护），`ruff check .` 全仓通过；
- 前端：`pnpm typecheck`、`pnpm test:run`（**192 passed**，含 zh/en 词条对称门禁）、
  `eslint --max-warnings 0` 全通过；
- E2E：`chat.e2e.ts` + `notice-push.e2e.ts` 在 chromium 与 webkit **隔离复跑全绿**；
  全量 `test:e2e:fresh` 234 passed / 2 flaky，其余失败集中在 webkit 后段跨模块
  （ai/analysis/dashboard/dform/webhook）——隔离复跑全部通过，属已知负载瞬态而非回归；
- 正式环境：`migrate`（建三表）+ `load_init_json`（1862 objects，6 权限点 + 4 内置角色授权）
  + `compilemessages` + 重启 `xadmin-server`/`xadmin-celery-worker`/`xadmin-celery-heavy`；
  核对：权限点路径与角色授权齐备、`/api/chat/*` 五个端点匿名访问返回 401（路由已注册而非 404）。

## 踩坑记录（2026-09-14）

1. **`SimpleRouter(False)` = 无尾斜杠**：项目所有 REST 前缀（system/notifications/mfa）都是
   无尾斜杠口径，权限点 `path` 也必须按 `api/chat/room$` 登记——写成带尾斜杠会全部 404，
   且表现为「权限点明明配了却打不通」。
2. **`async_to_sync` 里不能查库**：REST 侧广播若把 `ChatRoomMember.objects.filter(...)` 放进
   `@async_to_sync` 包裹的协程，Django 抛 `SynchronousOnlyOperation`（AI 接口曾整条 500）。
   正确姿势：先在同步上下文解析目标组（`room_event_groups`），再只把投递放进异步。
3. **协程里取 `UserConfig(pk).KEY` 会顺手查库**：`database_sync_to_async(UserConfig(pk).PUSH_CHAT_MESSAGE)`
   —— 属性访问在**构造参数时**就执行了（仍处协程），必须包一个独立同步函数
   （`can_push_chat`），否则站内信提醒链路在 WS 侧直接 500。
4. **`ChatNotify` 要覆盖 `ping`**：基类心跳只续期 `self.group_name`；聊天连接同时属于公共组与
   用户组，只续期一个会让另一个分组在 channel layer 的 30s 过期语义下失活。
5. **测试用内存 channel layer 要与生产同口径**：`update_active_layers` 旧的「按 `_` 尾段数字
   判在线」把 `chat_user_1` 误当在线用户；`get_groups` 返回全部组会把聊天组混进在线快照的
   降级路径（pks 为空但 sockets 非空）。两处都改为按 `websocket_group_` 前缀判定。
6. **测试库没有 `load_init_json`**：`PUSH_CHAT_MESSAGE` 依赖 `SystemConfig` 种子的 `inherit=true`
   才能被用户继承，缺种子时 `UserConfig(pk).PUSH_CHAT_MESSAGE` 回退成空 dict（视为关闭），
   提醒链路测试需显式造该配置行（fixture `chat_push_enabled`）。
7. **前端 i18n 词条里的 `@` 是保留语法**：vue-i18n 把 `@` 当「linked message」引用符，
   `chat.mentionHint: 输入 @ 可提及在线成员` 直接让 i18n 初始化抛
   `Invalid linked format (error code: 10)`，表现为**所有**引入 i18n 的单测文件一起失败。
   字面量 `@` 必须写成 `{'@'}`（与既有的尖括号 `<>`、行首 `{` 陷阱同一类）。
8. **`el-input`（textarea 形态）不保证 `data-testid` 落到内部 `<textarea>`**：E2E 用
   `[data-testid="chat-input"] textarea` 会一直等不到元素；data-testid 挂原生 wrapper div，
   或直接按 placeholder/role 定位。同理 icon-only 的抽屉开关按钮要显式 `aria-label`。

## 三期（2026-09-18）：多人群聊（突破「明确不做」的二期候选）

**背景**：原「后果」列把多人群聊登记为「明确不做（二期候选）」。产品优先级确认后启动
（长期优化方案 §4.4 聊天室行 / F4 按需功能池），按「先 ADR 后动手」在本 ADR 记录结论。

### 1. 数据与语义

- `ChatRoom.RoomType` 新增 `group`；`owner` 字段语义扩展为「AI 会话归属者 / 群主」；
  `room_key` 取 `group:{uuid4}`（**非幂等**：每次创建即新群，与 public/private 的幂等键不同）；
- 成员关系复用 `ChatRoomMember`（含未读游标）；群聊**创建即入会话列表**（`last_message_time`
  为空也可见，与私聊「有消息才列出」不同）；
- 规则：成员上限 `MAX_GROUP_MEMBERS = 200`（含群主）；名称必填 ≤64 字符；成员只接受在用用户，
  任一非法/失效成员整体拒绝（避免半成品群）；群主退出自动转让给**最早加入**的成员，
  末位成员退出即软删房间；非成员/非群聊一律按「会话不存在」拒绝（fail-closed）。

### 2. REST 与权限

| 端点 | 语义 | 权限点 |
|------|------|--------|
| `POST /api/chat/room/create-group` | 建群（创建者即群主，成员 ≥1 且不含自己） | `createGroup:ChatRoom` |
| `GET\|POST /api/chat/room/{pk}/members` | 查看完整成员（成员可见）/ 增删成员（仅群主） | `members:ChatRoom`（GET+POST 共享，登记 `SHARED_METHOD_PATHS`） |
| `POST /api/chat/room/{pk}/rename` | 改名（仅群主） | `rename:ChatRoom` |
| `POST /api/chat/room/{pk}/leave` | 退群（群主自动转让 / 末位解散） | `leave:ChatRoom` |
| `GET /api/chat/contacts/user-options` | 群成员候选（关键字搜索 ≤20 条，仅 pk/用户名/昵称） | 与该视图 `list` 权限同口径（`common/core/permission.py` 特例，存量角色免重授权） |

### 3. 通知与前端

- 群消息对**不在聊天室页面**的成员发站内信（`message_type="chat_group"`，受
  `PUSH_CHAT_MESSAGE` 偏好约束）；前端桌面通知把 `chat_group` 归入聊天类（前台也弹）；
  点击站内信直达该会话；
- 左栏会话区表头新建群聊入口（远程搜索选人，防抖 300ms）；会话行展示成员数；
  右栏群成员面板：群主可改名/增删成员，所有成员可退群（二次确认）。

### 4. 验收（2026-09-18）

- 后端：`tests/integration/message/test_chat_api.py` 新增 7 例（建群/入列、成员门槛、
  增删成员、非群主拒绝、改名、转让与解散、群未读与历史）+ 权限种子守护；全量 pytest
  **2610 collected exit 0**；`ws-frame.schema.json` 无新增 action（群聊复用 `chat_message`）；
- 前端：`pnpm test:run` 全量通过；`pnpm test:e2e --project=chromium e2e/chat.e2e.ts`
  新增「多人群聊建群、消息、成员管理与退群」用例（建群 → 发消息 → 成员面板 → 改名 →
  拉人 → 移除 → 退群）。
