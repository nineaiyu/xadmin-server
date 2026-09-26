# ADR-049：审批动作并发安全与 AI 工具层权限口径对齐

- 日期：2026-09-21
- 状态：**已交付**
- 关联：[ADR-038](ADR-038-ai-actions.md)（受限动作白名单）、[ADR-047](ADR-047-ai-console-persistence-and-tools.md)（助手页改版）、
  [ADR-048](ADR-048-ai-unified-tool-layer.md)（统一工具层）、`approval/utils/approval_flow/`、
  `ai/utils/ai_actions.py`、`common/core/permission.py`

## 背景

上线前全链路走查发现五类「跑不通 / 不一致」缺陷，均为**单元测试全绿但真实使用失败**型——
根因是测试视角（超管 + APIClient 默认 `Accept: */*` + 单进程串行）与用户视角
（普通角色 + 真实权限点 + 并发）不同源：

1. **AI 权限点正则在运行库是旧版**：`status:AiAssistant` 缺 `history|tools`、
   `actionExecute:AiAssistant` 缺 `interpret/stream` → 普通用户访问助手页历史 / 工具目录 /
   指令执行一律 403；超管因权限豁免不受影响（开发期「人工验证通过」具有欺骗性）。
2. **AI 动作业务权限预检与运行时访问控制不同源**：`user_can_visit` 只认菜单权限点，
   而 dashboard 系列端点在访问白名单（登录即可访问、本就无权限点）→ `dashboard.overview`
   对普通用户永久不可见 / 不可执行。
3. **声明式动作声明与真实路由方法不一致**：`task.enable` 声明 `POST` 而端点是 `PATCH`
   → 普通用户被权限预检拦下（不存在 POST 权限点）、超管内部 dispatch 405。
4. **审批引擎并发无互斥**：节点推进（`_advance` → 下一节点建任务组）与终态跃迁无原子保护
   → 两个审批人几乎同时通过时可能重复建任务组、重复投递 Webhook 与业务回调。
5. **演示角色无 AI 授权**：`seed_demo_org` 的 `ROLE_MENUS` 不含 AI 助手页 → 开箱体验
   「AI 不可用」（对演示/验收是致命的第一印象）。

## 决策

- **D1 权限预检与运行时同源**：抽 `common.core.permission.match_permission_white_url`
  （`IsAuthenticated` 与 AI 动作预检共用同一实现与同一配置），白名单端点按
  「登录即可访问」语义在预检中放行——两边必须同源，否则「运行时能访问、AI 动作无权限」
  类缺口必然重演。
- **D2 声明式动作双守护**（`test_ai_api_registry_guard.py`）：①`method` 与真实路由
  `view.actions` 一致性（错配不再静默 405）；②每个动作的 (method, path) 能在权限点种子中
  命中，或命中访问白名单（否则动作只对超管可用）。
- **D3 审批动作并发安全**：`approve_task` / `reject_task` / `cancel_instance` / `add_sign`
  统一 `transaction.atomic` + `select_for_update`（实例行锁；生产 Postgres 串行化，
  sqlite 忽略行锁退化为 CAS）；`_finish_instance` 改为状态 CAS（仅 `PENDING → 终态`），
  重复调用不再重复投递 Webhook / 业务回调 / 通知。新增 `TestConcurrencyGuard` 三例守护
  （重复通过不重复建任务组、重复驳回/撤回幂等、终态 CAS 只生效一次）。
- **D4 结构**：审批包拆出 `extra_actions.py`（催办 / 加签——不改变节点推进的参与者侧动作），
  `engine.py` 保留节点推进；跨模块调用走 `from . import engine` 属性访问，保持
  monkeypatch 单一 patch 点（避免 `from engine import _notify` 的导入时绑定失效）。
- **D5 演示开箱**：`seed_demo_org` 的 `ROLE_MENUS` 增 AI 助手页（其下权限点自动附带，
  AI 动作仍受业务权限双门约束）；`EXCLUDE_PERMISSION_NAMES` 排除 `mcp:AiMcp`
  （面向机器凭证的通道，不对业务角色开放）。
- **D6 运行库权限点同步**：seed 与运行库的权限点差异按「seed 为准」定向同步
  （path/method 不一致即更新，缺失即补齐，如 `mcp:AiMcp`），同步后清权限缓存。
  比对口径：同 name 比 path/method/deleted_at（同名不同 path 是运行期 403 的隐蔽来源）。
- **D7 前端 i18n 词条门禁**（`xadmin-client/scripts/check-i18n-keys.mjs`，接入 CI lint）：
  代码静态引用的词条必须同时存在于 zh/en，且两侧 key 集合完全对称——缺失词条不会报错，
  只会把 key 原文显示在界面上（历史问题：`ratioRangeRequired` / `buttons.confirm` /
  `userinfo.verifyPassword` 三处长期缺失）。

### 二批（审批能力补全 + AI 去重）

- **D8 转交（transfer）**：`extra_actions.transfer_task`——原任务置 CANCELLED（comment 注明
  转交给谁，保留在流转时间线），新任务 `delegate_from` = 原处理人（前端据此标注来源）；
  仅处理人本人或超管、仅 PENDING 任务/实例/当前节点；目标须启用且非申请人本人。
  端点 `POST api/system/approval-instances/{pk}/transfer` + 前端「转交」弹窗（SearchUser 选人）。
- **D9 管理视角（scope=ongoing）**：新增**无独立路由的功能授权权限点**
  `ongoing:SystemApprovalInstance`（列表端点带 scope 参数），后端 `user_has_permission`
  与运行时访问控制同口径校验（超管豁免），前端按 `hasAuth` 显示「全部在途」页签；
  列表序列化器新增 `current_assignees`（当前节点待办处理人昵称）供巡看「卡在谁那里」。
  该权限点通过 `seed_demo_org.EXCLUDE_PERMISSION_NAMES` 排除，不随页面授权下发给业务角色。
- **D10 加签节点语义收口**：或签（OR）节点下任一通过即流转、加签对推进无约束力
  （新候选会随首个通过被作废）→ 明确拒绝并引导使用转交；会签（AND）保持「新候选必须通过」；
  比例会签（RATIO）加签只抬高达标线不放松（数学上不会误伤提前驳回判定，测试固化）。
- **D11 AI 结构化输出客户端统一**：新增 `system.utils.ai.structured_chat_client()`
  （返回 `(client, max_tokens)`，max_tokens 缺省套用结构化安全上限），四条链路共用
  （聊天室 `/do`、助手页 `action/interpret/stream`、`nl-query/interpret` 与其流式版）；
  草稿确认摘要统一到 `ai_actions.draft_summary`（消除两处重复文案与 client 构建）。
- **D12 前端 SearchUser 载荷缺陷修复**：TSX 属性位置写 `onUpdate:modelValue` 会被解析为
  JSX 命名空间属性而不生效（须用 `h()` + 字符串键，项目既有惯例），且选择器 v-model 载荷是
  `{pk, label}`（label=username）而非 `{pk, username}`——两处叠加导致**加签/转交选人后提交空值**
  （报「请选择…」）。统一改为按 `username ?? label ?? value` 提取。

### 三批（原「产品侧候选」收口）

- **D13 批量转交（batch-transfer）**：`POST .../batch-transfer`，把勾选的多条待办一次转给
  同一用户；**逐条独立校验**（无待办/越权 pk 计入 `failures` 明细而非整体拒绝，全失败才整体
  1001）；取值域先经 `visible_instances_for` 收敛（越权 pk 不泄露存在性）。前端工具栏
  「批量转交」按钮（待办页签）＋弹窗（选人＋说明）＋部分失败明细提示。
- **D14 审批导出**：`OnlyExportDataAction.export_data` 挂 `OnlyExportDataAction` mixin 的
  ViewSet（如审批实例）复用列表链路（支持筛选），实例用**轻量导出序列化器**（不含 tasks/
  表单快照，`get_serializer_class` 按 action 切换——**覆写 export_data 会丢 `@action` 标记
  导致 404**）。**顺带修复全站既有缺陷**：内容协商按 `Accept: application/json` 一路落到
  renderers[0]，**`type=csv` 被忽略**（所有页面选 CSV 导出的文件实际是 xlsx）——改为在
  `export_data` 中按 type 显式绑定 `accepted_renderer`。
- **D15 比例会签达标线预览**：`queries.node_progress_for(instance)` 返回
  `{approve_type, approve_ratio, total, approved, pending, rejected, required, reached}`
  （required 与 engine 判定同源：AND=total、OR=1、RATIO=ceil(total×ratio%)，**加签抬升
  达标线**）；详情接口带出、前端详情抽屉渲染进度标签；加签成功后服务端回带新达标线，
  前端提示「已通过 X / 需 Y 人（候选 Z）」。
- **D16 种子 pk 冲突教训**：向 loadjson 追加权限点必须**全库校验 pk 唯一**——本轮
  ongoing 与 exportData 撞 pk（同 UUID 前缀手写自增尾段），loaddata 后者静默覆盖前者，
  表现为「某权限点时有时无」（chromium/webkit 环境差异实为两轮 loaddata 顺序差异）。
  修复后以 `pk 唯一 + menu.meta 引用无悬空` 自检，`test_permission_seed_coverage` 守护。

## 影响与验证

- **普通用户视角实测**（compose 环境，demo_staff）：`status` / `tools`（7 个可用动作，
  含 `dashboard.overview`，不含未授权的 `task.enable`）/ `ask`（真实模型回答带引用）/
  `nl-query` / 动作草稿 SSE / `history` 全 200，菜单出现 AI 入口；知识库管理页仍 403
  （设计如此——问答的 RAG 走服务端，不需要该页权限）。
- **审批两态全链路实测**：短假单节点（`days<=3` 条件跳过人事复核）与长假两节点均到
  `APPROVED`；OR 会签语义（任一通过 → 同节点其余任务 CANCELLED）实测正确。
- 后端全量 `pytest` 2967 passed + ruff + 行数/跨应用/缓存键/文档门禁；
  相关 E2E（AI / 审批 / 聊天 / 知识库 / 转交 / 管理视角）双浏览器通过；
  前端门禁全绿（typecheck / strict / eslint / prettier / stylelint / vitest /
  契约 / 包围 / 行数 / **i18n**）。
- 冒烟脚本（`scripts/smoke_ai_approval.py`，运行库 + 真实 HTTP + 普通用户 JWT）覆盖
  AI 4 项 + 审批 6 项 + 转交与管理视角 6 项，全部 PASS。
- 本轮（二批）交付后，原登记的遗留项「转交」「管理视角在途列表 + 超管催办」
  「加签在 OR/RATIO 语义」均已收口；产品侧后续候选：审批导出/统计口径扩展、
  转交的批量形态、加签在 RATIO 节点的「达标线预览」提示。
