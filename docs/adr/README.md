# 架构决策记录（ADR）索引

> 本目录是**历史决策记录**（"当时为什么这样选"），描述**现状**的文档请读
> [docs/README.md](../README.md)。新增决策按编号顺延并登记本索引。
>
> 二开常用速查：[ADR-027 代码生成器](ADR-027-code-generator.md)（`generate_crud`）、
> [ADR-045 模块化与裁剪](ADR-045-modular-trimmable-architecture.md)、
> [ADR-007 FormData 协议](ADR-007-multipart-form-data-v1-protocol.md)、
> [ADR-043 远程联想](ADR-043-remote-suggestions.md)、
> [ADR-035 契约治理](ADR-035-api-contract-governance.md)。

| ADR | 主题 |
|-----|------|
| [ADR-001](ADR-001-csrf-jwt-only.md)      | CSRF 中间件不启用（JWT-only 架构）           |
| [ADR-002](ADR-002-demo-app.md)           | demo app 去留：保留但默认关闭                |
| [ADR-003](ADR-003-websocket-protocol.md) | WebSocket 协议保持自定义格式并补类型约束          |
| [ADR-004](ADR-004-django-60-upgrade.md)  | Django 升级线：当前运行 6.0.8；6.1 被 beat 声明阻断（6.2 LTS 发布后按复审口径复核） |
| [ADR-005](ADR-005-redis-split.md)        | Redis 拆分（缓存/队列/会话分实例）              |
| [ADR-006](ADR-006-asgi-db-connection-pool.md) | ASGI 形态启用 Django server 端 DB 连接池      |
| [ADR-007](ADR-007-multipart-form-data-v1-protocol.md) | FormData 上传协议 v1（点分键序列化契约）       |
| [ADR-008](ADR-008-pat-auth.md)           | 个人访问令牌（PAT）：scope + 精确审计           |
| [ADR-009](ADR-009-data-mask-exemption.md) | 数据脱敏豁免清单机制                          |
| [ADR-010](ADR-010-crypto-es.md)          | crypto-js 弃用处置：替换为 crypto-es        |
| [ADR-011](ADR-011-aes-protocol-v2.md)    | 凭证加密协议升级 v2（WebCrypto PBKDF2+AES-GCM 双格式过渡） |
| [ADR-012](ADR-012-approval-flow-engine.md) | 审批流引擎（模板/实例/任务/加签/催办，含触发器与数据权限） |
| [ADR-013](ADR-013-office-online-preview.md) | Office 在线预览选型：LibreOffice headless 转 PDF（重队列 + 缓存回收） |
| [ADR-014](ADR-014-typescript-7-evaluation.md) | TypeScript 7 升级评估：暂不升级（vue-tsc 与 TS 7 不兼容，附实测数据） |
| [ADR-015](ADR-015-reference-project-adoption.md) | 参考项目借鉴决策：vue-pure-admin 点状移植边界 / 审批流可视化不引入 |
| [ADR-016](ADR-016-approval-flow-phase2.md) | 审批流引擎二期：节点出口路由（排他网关）/ 版本快照与回滚 / RATIO 比例会签 / @vue-flow 画布 |
| [ADR-017](ADR-017-ldap-directory-sync.md) | LDAP/AD 目录同步：bind 认证接入认证链（优先级可配、降级不阻断本地）/ OU→部门树 + 用户定时同步 / 冲突审计 / 凭据值级加密 |
| [ADR-018](ADR-018-im-scan-login.md) | 企业 IM 扫码登录：钉钉/企微/飞书 flavor 适配器（官方端点预设、企微 corp token 缓存）/ 绑定唯一与 MFA 回归沿用 / OAUTH_PROVIDERS 写侧校验接线 |
| [ADR-019](ADR-019-im-notify-channels.md) | 企业 IM 消息渠道：三家发送 SDK（token 缓存/unionId 换 userid）/ 收件账号按 flavor 复用 OAuth 绑定 / notify_im 配置值级加密 |
| [ADR-020](ADR-020-dataset-dashboard-phase1.md) | 数据集 + 仪表盘一期：模型/字段/op 白名单受控查询（行级数据权限 fail-closed）/ 布局 JSON + 四种图表卡片 / 个人·共享两档 / 评估出口条款 |
| [ADR-021](ADR-021-dashboard-display-and-reports.md) | 仪表盘二期 + 报表轻量版：Screen 大屏模板与全屏轮播（后端极薄）/ Report 定时报表（复用下载中心产物 + 邮件附件，创建者权限上下文）/ 明示不做边界 |
| [ADR-022](ADR-022-outbound-webhooks.md) | 出站 Webhook：事件目录 + 唯一发射口（吞异常）/ HMAC-SHA256 时间戳签名（secret 值级加密）/ 指数退避 5 次 + 耗尽告警 / 投递审计与重试 |
| [ADR-023](ADR-023-ai-assistant-phase1.md) | AI 一期（使用/二开助手）：OpenAI 兼容供应商中立接入层 / docs/ 分块入库 + 词频检索（向量升级候选池）/ ask 引用出处 / 权限门控 + 密钥值级加密（G12 模式先行） |
| [ADR-024](ADR-024-nl-query-phase2.md) | AI 二期 NL 查数：LLM 只产出受限数据集 DSL（LLM 输出按不可信输入处理）/ 服务端白名单重校验 + 数据权限 fail-closed / 试算预览 + 限幅 + 语义审计（AuthType.AI） / 灰度默认关 |
| [ADR-025](ADR-025-dynamic-form-phase1.md) | 动态表单一期：8 种收敛控件集 JSON Schema（写入/提交双侧校验）/ 通用 JSON 存储 + creator 隔离 / 零新依赖自定义动态表格（RePlusPage 动态列登记二期） |
| [ADR-026](ADR-026-dynamic-form-approval.md) | 动态表单二期（G5b）：表单定义开关 `approval_required` + 提交复用敏感操作审批协议（412 一次性令牌重放）/ 校验在前审批在后 / 超管直提（单管理员部署防死锁）/ 前端设计器开关与填报标记 |
| [ADR-027](ADR-027-code-generator.md) | 代码生成器（G7）：`generate_crud` 管理命令（Model → 序列化器/视图/路由/配置 + 前端页面 + 菜单种子）/ 生成块幂等合并 + import 去重 / 输出即过 ruff 门禁（生成器单测含 ruff 校验） |
| [ADR-028](ADR-028-global-search.md) | 全局搜索（G9）：顶栏搜索弹窗内跨实体分组结果（用户/部门/文件/审批单/日志）/ 逐实体两道门（页面权限门 + 数据权限编译器 fail-closed）/ 检索基线 icontains（转义反破坏匹配已实证，Postgres 全文化为评估出口）/ 权限码 retrieve:SystemGlobalSearch 入种子 |
| [ADR-029](ADR-029-page-watermark.md) | 敏感页面水印（G10）：基本设置三项配置（开关 / 文案 / 生效页面路由前缀）/ 文案含时间并分钟级刷新 / 挂载与清除收敛到 App.vue（移除 store 里的水印 hack），菜单级开关与防篡改列为评估出口 |
| [ADR-030](ADR-030-open-platform.md) | 开放平台雏形（G11）：`ApiApplication` 应用发卡机复用 PAT 认证链（sha256 口径/三层权限/审计）/ client-credentials 换发端点（明文仅一次、轮换即失效）/ 按应用限流（认证处计数 429）/ 回调注册 + HMAC 测试投递；不做应用级权限体系与 OAuth 授权码 |
| [ADR-031](ADR-031-multi-tenant-evaluation.md) | 多租户 go/no-go 评估（2027-09）：**结论 no-go（暂不做）**——一租户一实例为物理隔离、改造面 ≥6 窗口且与数据权限编译器高风险耦合；登记重开条件与 schema-per-tenant 预研要点 |
| [ADR-032](ADR-032-approval-business-integration.md) | 审批接入业务系统：通用业务绑定 `ApprovalInstance.biz_type/biz_id`（不引 ContentType）+ 终态回调 `approval_instance_finished` 信号（终态走 update 不触发 post_save）/ 首个真实业务「请假」（新增即提交、fail-closed 退化草稿、状态由终态回写、审批动作只在流程审批中心）/ 敏感操作审批挂载点扩到角色·部门删除（默认仍休眠）/ 字典·菜单·流程定义随种子下发 |
| [ADR-033](ADR-033-knowledge-base-management.md) | AI 知识库文档管理：`AiKnowledgeDocument` 双来源（repo/upload）统一登记 + 既有分块表即检索面（retrieve 零改动）/ 上传=文本入库（浏览器读文件，不落文件系统；同名覆盖更新）/ 预览=详情全文 + 分块摘要（列表轻量；原文展示不引 md 渲染依赖）/ 停用=移除分块、删除仅 upload / sync 只维护 repo（upload 前缀隔离 + 孤儿块清理，守护测试钉死） |
| [ADR-034](ADR-034-chat-room-rebuild.md) | 聊天室重构（微信式两栏）：`ChatRoom/ChatRoomMember/ChatMessage` 三表（room_key 幂等 + 未读游标 + client_msg_id 幂等 + 2 分钟撤回）/ 新通道 `ws/chat/`（显式组名、不登记会话、心跳不污染在线索引）/ `/api/chat/` 六接口 + 6 权限点 / 私聊与 AI 双形态（多轮 + `/kb` RAG 带引用）/ 前端两栏骨架（气泡/时间分组/游标加载/未读红点/@联想） |
| [ADR-035](ADR-035-api-contract-governance.md) | API 契约治理：**不做 URL 版本化（no-go，登记 3 条重开条件）**——消费者以同仓前端为主；契约唯一真源 `docs/schema/` + 前端镜像 `check:contract` + 服务端守护测试兜底；新路由 basename 统一 kebab-case、存量不改名（不影响权限链，仅监控 label 断档的纯 churn） |
| [ADR-036](ADR-036-import-export-replay-decision.md) | 导入导出 WSGIRequest 重放：**保留现状不重构**（与同步路径 100% 同源是既定意图，重构需先补装配契约测试）——装配点补 5 个隐式契约注释清单 + 登记重构步骤与重开条件 |
| [ADR-037](ADR-037-ai-retrieval-evaluation.md) | AI 检索升级评估（评测驱动）：**暂不引入向量**——36 问评测集入 CI 实测 hit@5 97.2%（535 块 / 30ms），远高于 75% 门控；登记评估出口（hit@5<75% / 分块>1000 / P95>300ms）与升级预研要点（OpenAI 兼容 embedding + Python 余弦 + RRF） |
| [ADR-038](ADR-038-ai-actions.md) | AI 助手受限动作（A2）：白名单动作注册表（请假/动态表单）+ 聊天 `/do` 草稿 + 确认卡片 + 权限双门 + 412 审批协议复用 + auth_type=ai 审计 + 灰度默认关；随项修复 SSE 端点浏览器 406 不可用的既有缺陷（EventStreamRenderer） |
| [ADR-039](ADR-039-open-platform-phase2.md) | 开放平台二期（B1–B4 全量）：应用级四级授权（模型×动作×字段×行，只收敛不提权）+ OAuth 授权码（PKCE/refresh/revoke/同意页）+ 用量报表与每日配额软告警 + Webhook 事件契约（schema_version + 自动文档 + 守护测试）；接入指南与示例客户端见 [open-platform/](../open-platform/README.md) |
| [ADR-040](ADR-040-approval-flow-phase3.md) | 审批流三期：**动作 MFA 二次确认已交付**（`APPROVAL_MFA_REQUIRED_ACTIONS` 逐动作灰度 + 412 `user_confirm_required` 复用 + 未验证不推进业务状态守护）；**委托代理已交付**（2026-09-15：委托表 + `resolve_assignees` 出口改造 + 不递归防环 + 审计标注代审） |
| [ADR-041](ADR-041-report-cron-expression.md) | 定时报表 cron 表达式：引入 `croniter`（纯 Python，pin 6.0.0）+ `Report.cron_expression`（非空覆盖三档频次）+ 分钟级判定（非法 fail-closed）+ 新增每分钟分发任务（与原每小时任务职责互斥，存量零变化） |
| [ADR-042](ADR-042-dashboard-card-permission.md) | 仪表盘卡片级权限（一二期全交付）：一期 `layout[].allowed_roles`（内嵌授权面，未知角色 code 拒绝）+ 读取侧按浏览者角色过滤（超管全量 / 匿名 fail-closed，只收敛不提权）；二期字段权限叠加到执行/聚合输出（无字段配置=全量的显式授权口径）+ 卡片弹窗「可见角色」授权 UI + 越权矩阵补强（18 例测试） |
| [ADR-043](ADR-043-remote-suggestions.md) | 远程联想（suggestions）：引用方 `SuggestionsAction`（`{prefix}/suggestions?field=`，候选集与写入校验同源，权限回落 list 权限点，零新权限点）+ ViewSet 级 `suggestion_fields` 字段白名单（元数据 `suggest_url` 与端点校验共用声明，含 with_meta=1 内联路径）+ 前端 `SuggestSelect`（remote/防抖/pks 回显）。首个消费方=审批委托「代理人」（委托人保持弹窗；部门管理经用户决策不采用）；菜单管理「自动添加API权限」登记为不适用场景 |
| [ADR-044](ADR-044-dform-approval-integration.md) | 动态表单与审批流集成（走查五项）：表单绑定审批流程（`approval_flow` + 提交状态/实例 + 终态回写 + 驳回重提）、审批通过自动完成提交（请求体快照 + 通过后动作注册表，multipart 仍走手动重放）、控件扩到 11 种（附件/日期范围/明细子表，禁嵌套）、部门授权写入修复（原静默丢弃）+ 数据权限 fail-closed 可诊断报错、`seed_demo_org` 开箱模板（组织+四层权限+场景模板） |
| [ADR-045](ADR-045-modular-trimmable-architecture.md) | 功能模块化与可裁剪架构（二开友好）：三级分层（core/standard/optional）+ 发行预设（**不做插件市场**）+ 模块声明单一事实源（内置 `MODULES` + app 侧 `{app}/modules.py` 扩展点）+ 六层裁剪（路由 404 / **WS 通道准入**（2026-09-18 增量）/ 菜单权限隐藏 / 周期任务不注册 / 种子裁剪 / 缓存清理）+ CLI（`modules` 清单预演、`module remove` 硬裁剪归档回滚、`generate_module` 脚手架）+ 只读「模块管理」页；默认 `full` 零行为差异；P2b/P4b 已评估关闭并登记触发条件 |
| [ADR-046](ADR-046-module-depth-completion.md) | 三大模块深度完善：表单草稿（DRAFT 轻校验 + `submit` 端点 + 操作审批自动落库与重放保序）与模板复用（`is_template` 同表 + `kind=templates`，不新增权限点）、数据字典驱动选项（schema `dict` 与内联 options 互斥，提交校验 fail-closed）、设计器字段排序与完整属性、提交详情与审批轨迹抽屉；数据分析修「可选但必失败」的度量字段（`numeric_columns`）、卡片错误可见化、看板刷新/设置、伪模型过滤与预览 CSV 导出；审批中心通过意见、人工催办（10 分钟节流）+ 流转时间线 + 代理标注（`delegate_from`）+ 驳回重提预填 + 分支路由 target 恒禁用缺陷修复 |
| [ADR-047](ADR-047-ai-console-persistence-and-tools.md) | AI 助手对话页改版：左右分栏（三入口导航按权限组装）、对话持久化（`AiChatMessage` 按 (creator, feature) 组织，流式 done/error 携带服务端载荷）、指令执行入口（`action/interpret/stream` 草稿流式 + 确认卡片 + 执行结果落库）、统一工具目录（`tools` 端点，MCP tools/list 等价 inputSchema）与五条声明式动作（user.search / notice.list / user.update / role.create / role.grant，配套 IN_QUERY / role / menu 参数类型与 `target` 改名）、思考面板滚动跟随与「思考中」去重 |
| [ADR-048](ADR-048-ai-unified-tool-layer.md) | AI 统一工具层：声明式动作注册表全量扩容（9 → 60 个动作，覆盖用户/组织/权限/公告/监控/数据集/任务/配置/日志/审批，按域拆分 registry 文件 + 路径可解析守护测试）、高危动作 AI 层强制审批与业务层审批穿透（`_approval_pre_authorized` 防双重审批死循环，超管豁免与 dform 同口径）、MCP 协议端点（`POST /api/system/ai/mcp`，JSON-RPC 2.0 无状态：initialize/tools/list/tools/call，PAT 认证，高危动作拒绝 + 全量审计）、多动作草稿串联（1~3 个/请求，`actions` 数组契约兼容旧单对象，前端 AiActionCard 复用 + testid 前缀保 E2E 选择器）；密码二次确认类端点（删除用户/重置 MFA/重置密码）明确排除 |
| [ADR-049](ADR-049-approval-concurrency-and-ai-permission-parity.md) | 审批动作并发安全与 AI 工具层权限口径对齐（上线前缺陷修复 + 二批能力补全）：权限预检与运行时白名单同源（`match_permission_white_url`）、声明式动作「method 一致性 + 权限点种子覆盖」双守护、审批动作统一实例行锁 + 终态 CAS、审批包拆 `extra_actions.py`、演示角色补 AI 章节授权（排除 MCP）、前端 i18n 词条门禁入 CI；**二批**：转交（transfer，原任务作废留痕 + 新任务来源标注）、管理视角「全部在途」（`ongoing:SystemApprovalInstance` 功能授权 + `current_assignees` 巡看列）、加签按节点类型收口（OR 明确拒绝）、AI 结构化输出客户端统一（`structured_chat_client`）、前端 SearchUser 载荷/事件缺陷修复；**三批**：批量转交（逐条独立 + 失败明细）、审批导出（轻量序列化器 + 修复全站 type=csv 被协商忽略的既有缺陷）、比例会签达标线预览（`node_progress_for` 与 engine 同源，加签抬升）、loadjson 追加条目 pk 唯一性教训 |

> 编号说明：ADR-050~055 编号已被早期草案占用后废弃，从未形成正式决策文档，编号不回收，新决策自 ADR-056 起顺延。

| [ADR-056](ADR-056-data-permission-config-and-scope.md) | 数据权限配置页重构与生效范围归一：新增/编辑改右侧抽屉 + 规则行内编辑卡片 + 可读摘要 + 未保存草稿试算；值控件形态由后端 `rule_meta` 下发（新增类型前端零改动）；菜单绑定保存时页面/目录展开为其下接口权限点（空展开拒绝）并补巡检 `[WARN]`；列表带出规则数/接口数/分配对象数（注解计数）；放开「自定义值/相对时间/指定部门」等取值方式；**合并语义维持不变**（授权池内并集取最宽、组内且/或、无授权空集、超管豁免），改为在界面显式说明并登记显式优先级为评估出口 |
| [ADR-057](ADR-057-system-app-domain-split.md) | system 巨型 app 按域拆分（approval / ai / dataset 三 app + 无状态工具下沉 common）：四道硬约束（表名/权限点路径/CT 与标签平移/运行期注册自持）+ SeparateDatabaseAndState 纯状态迁移 + services 契约门面重组；顺带修复跨 app 门禁横向扫描自诞生起空转的存量缺陷（src_app 取绝对路径首段恒 "/"）并收口暴露的 11 条真实违例；批次 4 容器文件同步滞后事故与恢复记录 |
| [ADR-058](ADR-058-system-migration-squash.md) | 2026 年度迁移合并（清库重建窗口）：system 0004~0019 八文件合为单一净增量 / approval·ai·dataset 0001 由零 DDL 认领改写真实建表（db_table 仍 system_*）/ notifications·demo 各合为单文件；存量库升级专用 RunPython（CT 改写·时间戳回填·显示名回填）退役，trgm 索引 state+受控执行按表归属拆入 system.0004 与 approval.0001（守护测试改双迁移合并快照）；顺带修复 loadjson 7 行 AI 模型节点陈旧标签（system.ai\* → ai.\*，ADR-057 同步漏网） |
| [ADR-059](ADR-059-app-aligned-url-prefixes.md) | URL 前缀与 app 对齐：approval / ai / dataset 三域独立挂载（`/api/approval/...`、`/api/ai/...`、`/api/dataset/...`，ADR-057「路径不变」约束解除）——权限点种子 / ModuleSpec 裁剪正则 / AI 工具声明与 triage / 搜索 list_url / PERMISSION_SHOW_PREFIX / 客户端 API 模块搬移与 395 处路径联动平移；契约镜像零改动 |
| [ADR-060](ADR-060-chat-attachment-messages.md) | 聊天附件消息（图片 / 文件）：消息只存引用（`ChatMessage.attachment` + `extra.file` 快照，取件 URL 由消息 pk 派生）；上传端点复用文件中心落库内核（`system/utils/upload_store.py`）并落临时件、随消息转正（`is_tmp=False`，避免每日临时清理误删）；WS `chat_message` 扩展 `message_type/file_pk`，服务端归属 fail-closed（只能引用本人上传件）；取件 `GET /api/chat/message/{pk}/file`（登录态 + 房间可访问，撤回后不可取，图片 `?size=thumb|preview` 走预览缓存） |
| [ADR-061](ADR-061-post-model.md) | 岗位（Post）模型：人员维度不参与权限判定（权限只经角色），软删除 + 未删除唯一；`UserInfo.posts` 多对多（一人可兼多岗）+ 关联计数声明式；查看（`members`）与分配（`assign`）拆两个端点两个权限点（单点权限只能绑定一种 HTTP 方法）；13 个权限点由 `sync_menu_permissions` 生成并登记 `PARENT_MENU_MAP`；AI triage 登记 exempt |
| [ADR-062](ADR-062-screen-canvas-designer.md) | 大屏画布设计器（P2.2 批次一）：`Screen.layout` 窗格模型（12 列 × 60 行栅格绝对定位，空 = 维持轮播回退）；三类组件（仪表盘 / 文本 / 时钟，窗格渲染与投屏共用 `ScreenPane`）；校验单一事实源在服务端（`dataset/utils/screen_layout.py`：类型白名单 / 越界 / 重叠 / 未知仪表盘 / 数量上限，未声明键丢弃），前端镜像几何口径做就地吸附与空位扫描（拖拽落点非法保持原位）；设计器为隐藏全屏页（左组件库 / 中画布 / 右属性面板 + 保存与预览）；刷新与导出在画布态逐窗格等价 |
| [ADR-063](ADR-063-report-designer.md) | 报表设计器（P2.2 批次二）：`Report.design`（明细列 + 行数上限 + 聚合组件，空 = 存量单表口径）；表格即「明细本体」不做成组件（`columns` 同时是导出口径）；校验与渲染同源（`dataset/utils/report_design.py`：列白名单 / 行数 10~500 / 组件 ≤12 且 id 唯一 / 图表必带分组 / sum·avg 必给数值列 / span∈{12,6}），读侧宽容（列被删改名与字段权限收紧不阻断投递）；投递时组件各落一张独立 sheet 且单组件失败 fail-soft；前端组件映射为看板卡片形态复用一期 `ChartCard`，模板（明细表 / 分组统计 / 趋势看板）作为设计起点 |
| [ADR-064](ADR-064-dform-designer-upgrade.md) | 动态表单设计器升级（P2.1）：字段间**联动规则**进 schema（扁平单目标规则 + 顺序覆盖求值；`hide/show/require/optional` × `eq/ne/in/notin/empty/notempty`；服务端提交校验联动优先=隐藏字段跳过校验且不落库、动态必填覆盖字段定义，前端镜像展示层）；**schema 版本化**（实质变更 +1、历史保留 20 版含全文与操作人、`schema-history` 查看 + `rollback` 回滚生成新版本、提交记录保存时版本）；写入侧顶层规范化（未声明键丢弃）；前端字段表拆组件 + sortablejs（fallback 走鼠标事件，回调撤销 DOM 位移后交 Vue 重排）+ 规则清单与版本弹窗；**不做在线建表** |
| [ADR-065](ADR-065-knowledge-vector-retrieval.md) | 知识库向量检索（P2.5，ADR-037 出口落地：语料 1178 块越过 1000 线）：启用条件=`purpose=embedding` 激活档案（**不做用途回落**，停用即回退词频）；向量落分块（float32 二进制 + model/hash/dim，陈旧判定显式跳过、分块重建按 content_hash 保留未变块向量）；构建独立触发（页面按钮 + `build_ai_embeddings`，幂等/force/dry-run，失败保留已完成批次）；检索=词频与向量 **RRF 融合**（k=60，权重 1.0/0.5 保基线优先，异常一律回退词频，`retrieve` 契约不变），CI 以假 embedding 守护 36 问 hit@5 ≥ 75%，桩 LLM 提供 `/v1/embeddings` 支撑 E2E |
| [ADR-066](ADR-066-mobile-form-factor.md) | 移动端形态评估（P2.6）：实测（iPhone 13 视口 390×664）核验 UA 判定下的移动形态——侧栏抽屉 / 页面移动分支 / 弹窗全屏化 / 表格容器内滚动（6 页页面级横向溢出 0）；**结论：维持「响应式优先」，不投独立 APP / uni-app**（登记三条重开条件）；修复实测缺陷 1 处（用户管理堆叠布局部门树铺满视口压掉列表 → `UserTree` 新增 `compact` 限高）；移动形态入 CI（`e2e/mobile.e2e.ts`：零溢出 + 抽屉开合 + 堆叠不遮挡）；边界：`deviceDetection()` 为 UA 判定，窄窗口与 iPad 不进移动形态 |
| [ADR-067](ADR-067-online-table-evaluation.md) | 在线建表评估（P2.1 第二段）：**结论：不投运行期 DDL**（表单即物理表）——成本/风险面（DDL 权限与回滚、字段变更数据迁移、PG/SQLite 方言、备份口径、数据权限编译器覆盖动态表、二开契约漂移）不成立，登记三条重开条件；现状能力基线（提交列表筛选 + `export-data` 动态列 + 数据集消费全链路）已覆盖多数消费场景，两个缺口按 **B2 表单数据页 → B1 数据集 JSON 路径列** 顺序登记触发条件，另登记「历史字段可见性（schema 快照）」改进项（**B2 已于 2026-09-27 交付，见 ADR-068**） |
| [ADR-068](ADR-068-form-data-management.md) | 表单数据（管理端只读数据面，ADR-067 B2 落地）：独立端点 `/api/dataset/form-data`（只读：列表 / 详情 / 导出 + 表单选项与选人回显动作；不给「我的填报」加管理视角——同 URL 无法区分两个权限点，且写路径保持单一）；行可见域 = 数据权限编译器（`PERMISSION_DATA_AUTH_APPS` 加入 `dataset`，超管全量 / 非超管按授权 fail-closed，授权菜单维度选「表单数据」或通用）；列表与详情契约分离（列表含 `data` 供动态列、详情含 schema 快照与审批轨迹）；导出复用 `SubmissionExportSerializer` 与 `export_dynamic_fields()`（抽到序列化器层供两条导出口径共用）；前端新页「表单数据」（选择表单 + schema 动态列 + 详情抽屉 + 导出，切换表单按 pk 重建表格），提交详情组件提升为表单域共享 |
| [ADR-069](ADR-069-dataset-json-columns.md) | 数据集 JSON 路径列（ADR-067 B1 落地）：列声明扩展 `字段.键`（`data.amount`）与 `字段.键\|number`（数值标注）；解析入口唯一（`dataset/utils/columns.py`，保存与执行双侧同源，类型白名单 / 段数与字符集 / 根字段必须是白名单 JSONField / 别名冲突全部 fail-closed）；能力面 = 明细 / 筛选 / 排序 / 分组 / sum·avg（表达式统一 Cast 到显式类型——裸 `KeyTextTransform` 参与比较会被按 JSON 文档准备值，SQLite 报 `malformed JSON`）；字段权限按根字段（`data.*` → `data`）收敛；**本段不做**：嵌套路径、日期类型标注与 `date_trunc` 趋势、`config.date_field`（均 fail-closed 并登记）；顺带修复拆分后种子「示例-表单提交」的 `bound_model` 失效（`system.` → `dataset.dynamicformsubmission`） |
| [ADR-070](ADR-070-dform-history-visibility.md) | 历史字段可见性（schema 版本快照解析）：以 `DynamicForm.schema_history`（保留 20 版全文）为唯一版本事实源，**不给提交冗余存快照**；新增 `dataset/utils/dform_history.py`（`schema_for_version` / `merged_fields` / `submission_schema`）统一三处口径——详情按**提交版本**渲染（当前已删字段标注历史）、导出列 = 当前 schema ∪ 涉及版本字段（旧值随行导出）、管理端「选择表单」schema 合并（列表动态列可见旧字段）；未知键（快照超窗 / 手工改库）以 key 兜底展示不回退为不可见；填报入口 `available-forms` 维持当前 schema（历史字段不回灌填报）；标注文案服务端 i18n，前端零改动 |
| [ADR-071](ADR-071-dataset-json-date-trend.md) | JSON 日期列与趋势分桶（ADR-069 D3 登记项落地）：类型标注新增 `\|date`（值契约 `YYYY-MM-DD`，不引入 `\|datetime`——控件集无 datetime 控件）；分桶走 **`Substr` 前缀截断**（month 7 / day 10 字符，桶名与模型字段路径同格式）而非 `Trunc+Cast`——SQLite 的 `CAST(x AS datetime)` 无类型亲和性会数值化、PG 需显式 `::timestamp`，两端方言不一致；`filters` 日期区间走 ISO 文本序（字典序 = 时间序）；`config.date_field` 支持 `data.x\|date`（须在 columns 内且带标注）；**写入侧加固**：顶层 `date` 字段提交校验收紧为 `DATE_RE`（与明细子表 date 列一致，此前只校验「是字符串」）；`daterange` 趋势与非 ISO 存量值落空桶登记为边界 |
| [ADR-072](ADR-072-observability-hardening.md) | 可观测与运维加固收口（三项登记出口集中关闭）：**①日志与响应脱敏四路同口径**——递归按敏感键掩码（含 `sure_password`）+ 脱敏先于截断，操作日志 `body` / `response_result`（新增，此前登录响应的 `access`/`refresh` 明文落库）/ DEBUG 正文（新增）/ 慢请求日志共用；**②配置类型契约**——实测 66 个系统级 property 零类型漂移即评估出口关闭，新增逐项守护 `test_config_type_contract.py`（含注入式负向验证），不改 `convert_type`；**③`data` 目录 750**——installer `prepare_config` 幂等收紧（仅目录位、三调用路径共用）；关联运维收口：冷归档生产演练 + 残留清理、AES v1 关闭后浏览器全流程验收、发布产物随版本核对、巨型文件台账口径同步 |
| [ADR-073](ADR-073-in-flight-flow-versioning.md) | 审批流在途实例绑版本（节点有效区间，P2 第一项）：节点行新增 `version_from`/`version_to`（改版 = 当前生效行收口 + 按新版本落新行，**不物理删除**），实例按自身 `flow_version` 过滤节点集推进（`all_objects.effective_at(V)`；空版本回退当前定义）；默认管理器只暴露当前生效行（管理面/序列化/计数天然隔离历史行），`(flow, order)` 无条件唯一改条件唯一（仅生效行之间，MySQL/MariaDB 不落库由写入路径行锁保证）；**解锁「有 PENDING 实例禁止改版」**（`_assert_nodes_mutable` 删除，改节点/回滚均放行，在途单走旧版、新单走新版）；发起在流程行锁内钉版本（与并发改版串行化），改版/回滚在流程行锁内分配版本号；存量行标「自 v1 起生效」、`flow.version<1` 回填，演示种子命令（场景模板/审批人重绑/卸载回滚）同步走版本化写入 |
