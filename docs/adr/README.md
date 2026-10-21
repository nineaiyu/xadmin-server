# 架构决策记录（ADR）索引

> 本目录是**历史决策记录**（"当时为什么这样选"），描述**现状**的文档请读
> [docs/README.md](../README.md)。新增决策按编号顺延并登记本索引。
>
> 二开常用速查：[ADR-027 代码生成器](ADR-027-code-generator.md)（`generate_crud`）、
> [ADR-045 模块化与裁剪](ADR-045-modular-trimmable-architecture.md)、
> [ADR-007 FormData 协议](ADR-007-multipart-form-data-v1-protocol.md)、
> [ADR-043 远程联想](ADR-043-remote-suggestions.md)、
> [ADR-035 契约治理](ADR-035-api-contract-governance.md)。

> **维护口径**：状态列为 已交付 / 已接受 / 暂不实施（触发制）/ 评估否 / 被取代；**主题列保持一句话索引**——实现细节写各 ADR 本体，勿在本表堆交付摘要（索引体积受 `scripts/check_doc_size.py` 的 40 KB 预算约束，超限即挂门禁）。

| ADR | 状态 | 主题 |
|-----|------|------|
| [ADR-001](ADR-001-csrf-jwt-only.md)      | 已接受 | CSRF 中间件不启用（JWT-only 架构）           |
| [ADR-002](ADR-002-demo-app.md)           | 已接受 | demo app 去留：保留但默认关闭                |
| [ADR-003](ADR-003-websocket-protocol.md) | 已接受 | WebSocket 协议保持自定义格式并补类型约束          |
| [ADR-004](ADR-004-django-60-upgrade.md)  | 已接受 | Django 升级线：当前运行 6.0.8；6.1 被 beat 声明阻断（6.2 LTS 发布后按复审口径复核） |
| [ADR-005](ADR-005-redis-split.md)        | 已接受 | Redis 拆分（缓存/队列/会话分实例）              |
| [ADR-006](ADR-006-asgi-db-connection-pool.md) | 已接受 | ASGI 形态启用 Django server 端 DB 连接池      |
| [ADR-007](ADR-007-multipart-form-data-v1-protocol.md) | 已接受 | FormData 上传协议 v1（点分键序列化契约）       |
| [ADR-008](ADR-008-pat-auth.md)           | 已接受 | 个人访问令牌（PAT）：scope + 精确审计           |
| [ADR-009](ADR-009-data-mask-exemption.md) | 已接受 | 数据脱敏豁免清单机制                          |
| [ADR-010](ADR-010-crypto-es.md)          | 已接受 | crypto-js 弃用处置：替换为 crypto-es        |
| [ADR-011](ADR-011-aes-protocol-v2.md)    | 已接受 | 凭证加密协议升级 v2（WebCrypto PBKDF2+AES-GCM 双格式过渡） |
| [ADR-012](ADR-012-approval-flow-engine.md) | 已接受 | 审批流引擎（模板/实例/任务/加签/催办，含触发器与数据权限） |
| [ADR-013](ADR-013-office-online-preview.md) | 已接受 | Office 在线预览选型：LibreOffice headless 转 PDF（重队列 + 缓存回收） |
| [ADR-014](ADR-014-typescript-7-evaluation.md) | 暂不实施（触发制） | TypeScript 7 升级评估：暂不升级（vue-tsc 与 TS 7 不兼容，附实测数据） |
| [ADR-015](ADR-015-reference-project-adoption.md) | 已接受 | 参考项目借鉴决策：vue-pure-admin 点状移植边界 / 审批流可视化不引入 |
| [ADR-016](ADR-016-approval-flow-phase2.md) | 已接受 | 审批流引擎二期：节点出口路由（排他网关）/ 版本快照与回滚 / RATIO 比例会签 / @vue-flow 画布 |
| [ADR-017](ADR-017-ldap-directory-sync.md) | 已接受 | LDAP/AD 目录同步：bind 认证接入认证链（优先级可配、降级不阻断本地）/ OU→部门树 + 用户定时同步 / 冲突审计 / 凭据值级加密 |
| [ADR-018](ADR-018-im-scan-login.md) | 已接受 | 企业 IM 扫码登录：钉钉/企微/飞书 flavor 适配器（官方端点预设、企微 corp token 缓存）/ 绑定唯一与 MFA 回归沿用 / OAUTH_PROVIDERS 写侧校验接线 |
| [ADR-019](ADR-019-im-notify-channels.md) | 已接受 | 企业 IM 消息渠道：三家发送 SDK（token 缓存/unionId 换 userid）/ 收件账号按 flavor 复用 OAuth 绑定 / notify_im 配置值级加密 |
| [ADR-020](ADR-020-dataset-dashboard-phase1.md) | 已接受 | 数据集 + 仪表盘一期：模型/字段/op 白名单受控查询（行级数据权限 fail-closed）/ 布局 JSON + 四种图表卡片 / 个人·共享两档 / 评估出口条款 |
| [ADR-021](ADR-021-dashboard-display-and-reports.md) | 已接受 | 仪表盘二期 + 报表轻量版：Screen 大屏模板与全屏轮播（后端极薄）/ Report 定时报表（复用下载中心产物 + 邮件附件，创建者权限上下文）/ 明示不做边界 |
| [ADR-022](ADR-022-outbound-webhooks.md) | 已接受 | 出站 Webhook：事件目录 + 唯一发射口（吞异常）/ HMAC-SHA256 时间戳签名（secret 值级加密）/ 指数退避 5 次 + 耗尽告警 / 投递审计与重试 |
| [ADR-023](ADR-023-ai-assistant-phase1.md) | 已接受 | AI 一期（使用/二开助手）：OpenAI 兼容供应商中立接入层 / docs/ 分块入库 + 词频检索（向量升级候选池）/ ask 引用出处 / 权限门控 + 密钥值级加密（G12 模式先行） |
| [ADR-024](ADR-024-nl-query-phase2.md) | 已接受 | AI 二期 NL 查数：LLM 只产出受限数据集 DSL（LLM 输出按不可信输入处理）/ 服务端白名单重校验 + 数据权限 fail-closed / 试算预览 + 限幅 + 语义审计（AuthType.AI） / 灰度默认关 |
| [ADR-025](ADR-025-dynamic-form-phase1.md) | 已接受 | 动态表单一期：8 种收敛控件集 JSON Schema（写入/提交双侧校验）/ 通用 JSON 存储 + creator 隔离 / 零新依赖自定义动态表格（RePlusPage 动态列登记二期） |
| [ADR-026](ADR-026-dynamic-form-approval.md) | 已接受 | 动态表单二期（G5b）：表单定义开关 `approval_required` + 提交复用敏感操作审批协议（412 一次性令牌重放）/ 校验在前审批在后 / 超管直提（单管理员部署防死锁）/ 前端设计器开关与填报标记 |
| [ADR-027](ADR-027-code-generator.md) | 已接受 | 代码生成器（G7）：`generate_crud` 管理命令（Model → 序列化器/视图/路由/配置 + 前端页面 + 菜单种子）/ 生成块幂等合并 + import 去重 / 输出即过 ruff 门禁（生成器单测含 ruff 校验） |
| [ADR-028](ADR-028-global-search.md) | 已接受 | 全局搜索（G9）：顶栏搜索弹窗内跨实体分组结果（用户/部门/文件/审批单/日志）/ 逐实体两道门（页面权限门 + 数据权限编译器 fail-closed）/ 检索基线 icontains（转义反破坏匹配已实证，Postgres 全文化为评估出口）/ 权限码 retrieve:SystemGlobalSearch 入种子 |
| [ADR-029](ADR-029-page-watermark.md) | 已接受 | 敏感页面水印（G10）：基本设置三项配置（开关 / 文案 / 生效页面路由前缀）/ 文案含时间并分钟级刷新 / 挂载与清除收敛到 App.vue（移除 store 里的水印 hack），菜单级开关与防篡改列为评估出口 |
| [ADR-030](ADR-030-open-platform.md) | 已接受 | 开放平台雏形（G11）：`ApiApplication` 应用发卡机复用 PAT 认证链（sha256 口径/三层权限/审计）/ client-credentials 换发端点（明文仅一次、轮换即失效）/ 按应用限流（认证处计数 429）/ 回调注册 + HMAC 测试投递；不做应用级权限体系与 OAuth 授权码 |
| [ADR-031](ADR-031-multi-tenant-evaluation.md) | 已接受 | 多租户 go/no-go 评估（2027-09）：**结论 no-go（暂不做）**——一租户一实例为物理隔离、改造面 ≥6 窗口且与数据权限编译器高风险耦合；登记重开条件与 schema-per-tenant 预研要点 |
| [ADR-032](ADR-032-approval-business-integration.md) | 已接受 | 审批接入业务系统：通用业务绑定 `ApprovalInstance.biz_type/biz_id`（不引 ContentType）+ 终态回调 `approval_instance_finished` 信号（终态走 update 不触发 post_save）/ 首个真实业务「请假」（新增即提交、fail-closed 退化草稿、状态由终态回写、审批动作只在流程审批中心）/ 敏感操作审批挂载点扩到角色·部门删除（默认仍休眠）/ 字典·菜单·流程定义随种子下发 |
| [ADR-033](ADR-033-knowledge-base-management.md) | 已接受 | AI 知识库文档管理：`AiKnowledgeDocument` 双来源（repo/upload）统一登记 + 既有分块表即检索面（retrieve 零改动）/ 上传=文本入库（浏览器读文件，不落文件系统；同名覆盖更新）/ 预览=详情全文 + 分块摘要（列表轻量；原文展示不引 md 渲染依赖）/ 停用=移除分块、删除仅 upload / sync 只维护 repo（upload 前缀隔离 + 孤儿块清理，守护测试钉死） |
| [ADR-034](ADR-034-chat-room-rebuild.md) | 已接受 | 聊天室重构（微信式两栏）：`ChatRoom/ChatRoomMember/ChatMessage` 三表（room_key 幂等 + 未读游标 + client_msg_id 幂等 + 2 分钟撤回）/ 新通道 `ws/chat/`（显式组名、不登记会话、心跳不污染在线索引）/ `/api/chat/` 六接口 + 6 权限点 / 私聊与 AI 双形态（多轮 + `/kb` RAG 带引用）/ 前端两栏骨架（气泡/时间分组/游标加载/未读红点/@联想） |
| [ADR-035](ADR-035-api-contract-governance.md) | 已接受 | API 契约治理：**不做 URL 版本化（no-go，登记 3 条重开条件）**——消费者以同仓前端为主；契约唯一真源 `docs/schema/` + 前端镜像 `check:contract` + 服务端守护测试兜底；新路由 basename 统一 kebab-case、存量不改名（不影响权限链，仅监控 label 断档的纯 churn） |
| [ADR-036](ADR-036-import-export-replay-decision.md) | 已接受 | 导入导出 WSGIRequest 重放：**保留现状不重构**（与同步路径 100% 同源是既定意图，重构需先补装配契约测试）——装配点补 5 个隐式契约注释清单 + 登记重构步骤与重开条件 |
| [ADR-037](ADR-037-ai-retrieval-evaluation.md) | 已接受 | AI 检索升级评估（评测驱动）：**暂不引入向量**——36 问评测集入 CI 实测 hit@5 97.2%（535 块 / 30ms），远高于 75% 门控；登记评估出口（hit@5<75% / 分块>1000 / P95>300ms）与升级预研要点（OpenAI 兼容 embedding + Python 余弦 + RRF） |
| [ADR-038](ADR-038-ai-actions.md) | 已接受 | AI 助手受限动作（A2）：白名单动作注册表（请假/动态表单）+ 聊天 `/do` 草稿 + 确认卡片 + 权限双门 + 412 审批协议复用 + auth_type=ai 审计 + 灰度默认关；随项修复 SSE 端点浏览器 406 不可用的既有缺陷（EventStreamRenderer） |
| [ADR-039](ADR-039-open-platform-phase2.md) | 已接受 | 开放平台二期（B1–B4 全量）：应用级四级授权（模型×动作×字段×行，只收敛不提权）+ OAuth 授权码（PKCE/refresh/revoke/同意页）+ 用量报表与每日配额软告警 + Webhook 事件契约（schema_version + 自动文档 + 守护测试）；接入指南与示例客户端见 [open-platform/](../open-platform/README.md) |
| [ADR-040](ADR-040-approval-flow-phase3.md) | 已交付 | 审批流三期：**动作 MFA 二次确认已交付**（`APPROVAL_MFA_REQUIRED_ACTIONS` 逐动作灰度 + 412 `user_confirm_required` 复用 + 未验证不推进业务状态守护）；**委托代理已交付**（2026-09-15：委托表 + `resolve_assignees` 出口改造 + 不递归防环 + 审计标注代审） |
| [ADR-041](ADR-041-report-cron-expression.md) | 已交付 | 定时报表 cron 表达式：引入 `croniter`（纯 Python，pin 6.0.0）+ `Report.cron_expression`（非空覆盖三档频次）+ 分钟级判定（非法 fail-closed）+ 新增每分钟分发任务（与原每小时任务职责互斥，存量零变化） |
| [ADR-042](ADR-042-dashboard-card-permission.md) | 已交付 | 仪表盘卡片级权限（一二期全交付）：一期 `layout[].allowed_roles`（内嵌授权面，未知角色 code 拒绝）+ 读取侧按浏览者角色过滤（超管全量 / 匿名 fail-closed，只收敛不提权）；二期字段权限叠加到执行/聚合输出（无字段配置=全量的显式授权口径）+ 卡片弹窗「可见角色」授权 UI + 越权矩阵补强（18 例测试） |
| [ADR-043](ADR-043-remote-suggestions.md) | 已交付 | 远程联想（suggestions）：引用方 `SuggestionsAction`（`{prefix}/suggestions?field=`，候选集与写入校验同源，权限回落 list 权限点，零新权限点）+ ViewSet 级 `suggestion_fields` 字段白名单（元数据 `suggest_url` 与端点校验共用声明，含 with_meta=1 内联路径）+ 前端 `SuggestSelect`（remote/防抖/pks 回显）。首个消费方=审批委托「代理人」（委托人保持弹窗；部门管理经用户决策不采用）；菜单管理「自动添加API权限」登记为不适用场景 |
| [ADR-044](ADR-044-dform-approval-integration.md) | 已交付 | 动态表单与审批流集成（走查五项）：表单绑定审批流程（`approval_flow` + 提交状态/实例 + 终态回写 + 驳回重提）、审批通过自动完成提交（请求体快照 + 通过后动作注册表，multipart 仍走手动重放）、控件扩到 11 种（附件/日期范围/明细子表，禁嵌套）、部门授权写入修复（原静默丢弃）+ 数据权限 fail-closed 可诊断报错、`seed_demo_org` 开箱模板（组织+四层权限+场景模板） |
| [ADR-045](ADR-045-modular-trimmable-architecture.md) | 已交付 | 功能模块化与可裁剪架构（二开友好）：三级分层（core/standard/optional）+ 发行预设（**不做插件市场**）+ 模块声明单一事实源（内置 `MODULES` + app 侧 `{app}/modules.py` 扩展点）+ 六层裁剪（路由 404 / **WS 通道准入**（2026-09-18 增量）/ 菜单权限隐藏 / 周期任务不注册 / 种子裁剪 / 缓存清理）+ CLI（`modules` 清单预演、`module remove` 硬裁剪归档回滚、`generate_module` 脚手架）+ 只读「模块管理」页；默认 `full` 零行为差异；P2b/P4b 已评估关闭并登记触发条件 |
| [ADR-046](ADR-046-module-depth-completion.md) | 已交付 | 三大模块深度完善：表单草稿与模板复用、数据字典驱动选项、设计器字段排序与属性、提交详情与审批轨迹抽屉；数据分析度量字段修复与看板刷新；审批中心催办 / 流转时间线 / 代理标注 / 驳回重提 |
| [ADR-047](ADR-047-ai-console-persistence-and-tools.md) | 已接受 | AI 助手对话页改版：左右分栏（三入口导航按权限组装）、对话持久化（`AiChatMessage` 按 (creator, feature) 组织，流式 done/error 携带服务端载荷）、指令执行入口（`action/interpret/stream` 草稿流式 + 确认卡片 + 执行结果落库）、统一工具目录（`tools` 端点，MCP tools/list 等价 inputSchema）与五条声明式动作（user.search / notice.list / user.update / role.create / role.grant，配套 IN_QUERY / role / menu 参数类型与 `target` 改名）、思考面板滚动跟随与「思考中」去重 |
| [ADR-048](ADR-048-ai-unified-tool-layer.md) | 已交付 | AI 统一工具层：声明式动作注册表扩容（9 → 60）、高危动作 AI 层强制审批与业务层穿透（防双重审批）、MCP 协议端点（JSON-RPC 2.0）、多动作草稿串联 |
| [ADR-049](ADR-049-approval-concurrency-and-ai-permission-parity.md) | 已交付 | 审批动作并发安全与 AI 工具层权限口径对齐：实例行锁 + 终态 CAS、权限预检与运行时白名单同源；二 / 三批补转交与批量转交、管理视角「全部在途」、加签按节点类型收口、审批导出、比例会签达标线预览 |

> 编号说明：ADR-050~055 编号已被早期草案占用后废弃，从未形成正式决策文档，编号不回收，新决策自 ADR-056 起顺延。

| [ADR-056](ADR-056-data-permission-config-and-scope.md) | 已交付 | 数据权限配置页重构与生效范围归一：抽屉式编辑 + 规则行内卡片 + 草稿试算、值控件由 `rule_meta` 下发、菜单绑定展开接口权限点、合并语义界面显式说明 |
| [ADR-057](ADR-057-system-app-domain-split.md) | 已交付 | system 巨型 app 按域拆分（approval / ai / dataset 三 app + 无状态工具下沉 common）：四道硬约束（表名/权限点路径/CT 与标签平移/运行期注册自持）+ SeparateDatabaseAndState 纯状态迁移 + services 契约门面重组；顺带修复跨 app 门禁横向扫描自诞生起空转的存量缺陷（src_app 取绝对路径首段恒 "/"）并收口暴露的 11 条真实违例；批次 4 容器文件同步滞后事故与恢复记录 |
| [ADR-058](ADR-058-system-migration-squash.md) | 已交付 | 2026 年度迁移合并（清库重建窗口）：system 0004~0019 八文件合为单一净增量 / approval·ai·dataset 0001 由零 DDL 认领改写真实建表（db_table 仍 system_*）/ notifications·demo 各合为单文件；存量库升级专用 RunPython（CT 改写·时间戳回填·显示名回填）退役，trgm 索引 state+受控执行按表归属拆入 system.0004 与 approval.0001（守护测试改双迁移合并快照）；顺带修复 loadjson 7 行 AI 模型节点陈旧标签（system.ai\* → ai.\*，ADR-057 同步漏网） |
| [ADR-059](ADR-059-app-aligned-url-prefixes.md) | 已交付 | URL 前缀与 app 对齐：approval / ai / dataset 三域独立挂载（`/api/approval/...`、`/api/ai/...`、`/api/dataset/...`，ADR-057「路径不变」约束解除）——权限点种子 / ModuleSpec 裁剪正则 / AI 工具声明与 triage / 搜索 list_url / PERMISSION_SHOW_PREFIX / 客户端 API 模块搬移与 395 处路径联动平移；契约镜像零改动 |
| [ADR-060](ADR-060-chat-attachment-messages.md) | 已交付 | 聊天附件消息（图片 / 文件）：消息只存引用（`ChatMessage.attachment` + `extra.file` 快照，取件 URL 由消息 pk 派生）；上传端点复用文件中心落库内核（`system/utils/upload_store.py`）并落临时件、随消息转正（`is_tmp=False`，避免每日临时清理误删）；WS `chat_message` 扩展 `message_type/file_pk`，服务端归属 fail-closed（只能引用本人上传件）；取件 `GET /api/chat/message/{pk}/file`（登录态 + 房间可访问，撤回后不可取，图片 `?size=thumb|
| [ADR-061](ADR-061-post-model.md) | 已交付 | 岗位（Post）模型：人员维度不参与权限判定（权限只经角色），软删除 + 未删除唯一；`UserInfo.posts` 多对多（一人可兼多岗）+ 关联计数声明式；查看（`members`）与分配（`assign`）拆两个端点两个权限点（单点权限只能绑定一种 HTTP 方法）；13 个权限点由 `sync_menu_permissions` 生成并登记 `PARENT_MENU_MAP`；AI triage 登记 exempt |
| [ADR-062](ADR-062-screen-canvas-designer.md) | 已交付 | 大屏画布设计器（P2.2 批次一）：`Screen.layout` 窗格模型（12 列 × 60 行栅格绝对定位，空 = 维持轮播回退）；三类组件（仪表盘 / 文本 / 时钟，窗格渲染与投屏共用 `ScreenPane`）；校验单一事实源在服务端（`dataset/utils/screen_layout.py`：类型白名单 / 越界 / 重叠 / 未知仪表盘 / 数量上限，未声明键丢弃），前端镜像几何口径做就地吸附与空位扫描（拖拽落点非法保持原位）；设计器为隐藏全屏页（左组件库 / 中画布 / 右属性面板 + 保存与预览）；刷新与导出在画布态逐窗格等价 |
| [ADR-063](ADR-063-report-designer.md) | 已交付 | 报表设计器（P2.2）：`Report.design`（明细列 + 行数上限 + 聚合组件）、校验与渲染同源（`report_design.py`）、投递组件各落独立 sheet 且单组件失败 fail-soft、前端复用看板 ChartCard |
| [ADR-064](ADR-064-dform-designer-upgrade.md) | 已交付 | 动态表单设计器升级（P2.1）：字段联动规则进 schema（顺序覆盖求值、服务端校验联动优先）、schema 版本化（保留 20 版 + 历史查看 / 回滚）、前端字段表拆组件 + sortablejs；不做在线建表 |
| [ADR-065](ADR-065-knowledge-vector-retrieval.md) | 已交付 | 知识库向量检索（P2.5）：`purpose=embedding` 激活档案（不做用途回落）、向量落分块（float32 二进制 + 陈旧判定）、词频与向量 RRF 融合（异常回退词频）、CI 以假 embedding 守护 36 问 hit@5 |
| [ADR-066](ADR-066-mobile-form-factor.md) | 已交付 | 移动端形态评估（P2.6）：实测（iPhone 13 视口 390×664）核验 UA 判定下的移动形态——侧栏抽屉 / 页面移动分支 / 弹窗全屏化 / 表格容器内滚动（6 页页面级横向溢出 0）；**结论：维持「响应式优先」，不投独立 APP / uni-app**（登记三条重开条件）；修复实测缺陷 1 处（用户管理堆叠布局部门树铺满视口压掉列表 → `UserTree` 新增 `compact` 限高）；移动形态入 CI（`e2e/mobile.e2e.ts`：零溢出 + 抽屉开合 + 堆叠不遮挡）；边界：`deviceDetection()` 为 UA 判定，窄窗口与 iPad 不进移动形态 |
| [ADR-067](ADR-067-online-table-evaluation.md) | 评估否 | 在线建表评估：**结论不投运行期 DDL**（表单即物理表）+ 三条重开条件；现状能力已覆盖多数场景，缺口按「B2 表单数据页 → B1 数据集 JSON 路径列」登记（B2 已交付，见 ADR-068） |
| [ADR-068](ADR-068-form-data-management.md) | 已交付 | 表单数据（管理端只读数据面，ADR-067 B2）：独立端点 `/api/dataset/form-data`、行可见域 = 数据权限编译器（fail-closed）、列表与详情契约分离、导出复用 `SubmissionExportSerializer` |
| [ADR-069](ADR-069-dataset-json-columns.md) | 已交付 | 数据集 JSON 路径列（ADR-067 B1）：列声明 `字段.键` 与 `字段.键\|number` 数值标注、解析入口唯一（`dataset/utils/columns.py`）保存与执行双侧同源、字段权限按根字段收敛；嵌套路径 / 日期标注登记不做 |
| [ADR-070](ADR-070-dform-history-visibility.md) | 已交付 | 历史字段可见性（schema 版本快照解析）：以 `DynamicForm.schema_history`（保留 20 版）为唯一版本事实源、`dform_history.py` 统一三处口径（详情按提交版本渲染 / 导出列并集 / 选择表单 schema 合并）、未知键 key 兜底 |
| [ADR-071](ADR-071-dataset-json-date-trend.md) | 已交付 | JSON 日期列与趋势分桶（ADR-069 D3）：类型标注 `\|date`（值契约 YYYY-MM-DD）、分桶走 `Substr` 前缀截断（跨方言一致）、`filters` 日期区间 ISO 文本序、写入侧顶层 date 校验收紧为 `DATE_RE` |
| [ADR-072](ADR-072-observability-hardening.md) | 已交付 | 可观测与运维加固收口：日志与响应脱敏四路同口径（脱敏先于截断，含登录响应与 DEBUG 正文）、配置类型契约实测零漂移 + 逐项守护测试、`data` 目录 750 幂等收紧；冷归档演练 / AES v1 验收 / 发布产物核对等运维收口 |
| [ADR-073](ADR-073-in-flight-flow-versioning.md) | 已交付 | 审批流在途实例绑版本（节点有效区间，P2）：节点行 `version_from/version_to`（改版不物理删除）、实例按自身 `flow_version` 过滤节点集推进、默认管理器只暴露当前生效行、**解锁「有 PENDING 实例禁止改版」**、发起在流程行锁内钉版本 |
| [ADR-074](ADR-074-knowledge-pgvector.md) | 已交付 | 知识库检索 pgvector 演进（**已交付**，2026-10-02）：pgvector 扩展 + 无维度 `vector` 列（模型可换档，维度稳定才定型 `vector(N)` + HNSW `vector_cosine_ops` m=16/ef=64，混存窗口反向定型）+ 内存索引退役改 SQL 余弦（新鲜/模型/维度一致全在 WHERE 收敛，异常回退词频）+ RRF 融合层与 `retrieve` 契约零变化 + 二进制列双写一个版本窗口（回滚 = 代码回退）+ 存量 500 批回填；镜像全栈换 pgvector 变体（旧 postgres:17 上禁跑 0005 迁移）；**明确不做**独立向量库（Milvus/Qdrant）与多后端抽象 |
| [ADR-075](ADR-075-dform-filter-materialization.md) | 已交付 | 动态表单可筛选字段物化（ADR-067 后续项）：设计器勾选「可筛选」→ 提交物化 `filter_data`、列表筛选编译为单条 JSON 包含查询（PostgreSQL 命中 GIN）、五条写入路径统一走 `build_filter_data`、筛选口径 fail-closed、`rebuild_dform_filter_data` 兼容存量 |
| [ADR-076](ADR-076-ai-cost-accounting.md) | 暂不实施（触发制） | AI 计费与成本核算（**触发制登记，暂不实施**）：结论——单组织形态无结算受众（与 ADR-031 同一商业化逻辑）、支出在供应商侧可查、量级治理（三级配额）已在位；实施预研已固化（模型定价表按模型精确匹配 → 记账户成本快照（未定价 NULL 与零价区分、改价不重算历史）→ 汇总扩展与定价管理面）；触发条件（部门结算 / 云支出限额治理 / 二开需求）满足再立项 |
| [ADR-077](ADR-077-department-level-autonomy.md) | 已交付 | 部门级自治（三项补齐，**已交付**）：①部门管理员实体（`DeptInfo.managers` + `value.manager.*` 数据权限规则族 + 任命端点一步装配「内置角色 + 用户级预置规则 + 字段白名单」）②写侧载荷范围校验（归属字段限数据权限可见范围 + 关系字段过可授权池，补 `UserSerializer` 缺口）③边界保证（**不引入上限语义**——装配收敛 + 既有取值域约束 + `[宽授权]` 巡检告警，ceiling 登记评估出口）；单租户内范围收敛，不提供租户隔离（ADR-031 结论不变） |
| [ADR-078](ADR-078-asgi-async-middleware.md) | 已交付 | ASGI 中间件链异步化（杠杆 B，**已交付**）：四个生产链中间件双模化（`sync_capable`+`async_capable`，MiddlewareMixin 官方形态 `__call__`/`__acall__` 成对）消除中间件段线程交接；`common/local.py` 改 contextvars 存储（asgiref sync_to_async 复制 context 使事件循环写入的 `current_request` 对同步视图可见——D1 前置；跨裸线程隔离语义不变）；DEBUG-only 三项（Start/End/SQLCount）显式保持 sync；ApiLogging 深度异步化另册登记 |
| [ADR-079](ADR-079-contract-seam-interface.md) | 已交付 | common→业务 app 契约缝显式接口化（单缝收敛）：`common/contracts.py` 为框架层消费业务 app 的唯一出口（39 契约名声明式白名单 + PEP 562 惰性解析）、门禁升级为「除 contracts.py 外模块级业务 import 即违例」、31 缝 / 26 文件收敛为 1 文件 5 缝 |
| [ADR-080](ADR-080-table-domain-rename.md) | 已交付 | 物理表名归域改名（TG-3 收口）：解除拆分冻结——25 模型 + 3 自动 m2m 表 `system_*` → Django 默认归域名 `<app>_<模型>`、各 app 单迁移 `AlterModelTable`、守护 `test_table_domain_alignment.py`、存量升级模拟 + 全新链重放双路验证 |
| [ADR-081](ADR-081-contract-provider-injection.md) | 已交付 | 契约出口升级为可注入提供方（TG-5 收口）：`register_contract`（AppConfig.ready）或 entry points 注入、解析序 = 注入覆盖 → 白名单默认 → 未声明报错、白名单外拒绝注入 + 重复注册 fail-fast、25 个消费文件零改动；common 独立发包维持触发制 |
| [ADR-082](ADR-082-dormant-capabilities-activation.md) | 已交付 | 六项休眠能力激活（F7 收口，逐子项决策制）：AI 四开关与 `METRICS_ENABLED` 灰度转正（「配置即亮」）、`CHAT_HISTORY_DAYS`→365 / `FILE_KEEP_DAYS`→180 卫生默认值、渠道集成开关维持默认关、**向量自动重算落地**（三挂点增量补齐陈旧块） |
| [ADR-083](ADR-083-metadata-input-type-contract.md) | 已交付 | 元数据 input_type 词表冻结为稳定公共契约：真源 `DECLARED_INPUT_TYPES`（32 类型）+ `api-*` 开放族 + `x-fallback-rendered` 回退登记三段式、三面守护（载荷闭包 / Schema 锁步 / 跨栈覆盖）、扩展流程登记制 |
| [ADR-084](ADR-084-migration-squash-2026h2.md) | 已交付 | 2026-10 迁移合并（清库重建窗口，**已交付**）：ADR-058 后新增的 2026 年度迁移 24 个按 app 合并——system 0004~0011 合并为单一净增量、dataset/approval/ai/message 全链合并为真实建表 0001_initial（表名即现名，改名迁移退役）、notifications 0003+0004 合并；迁移文件 47 → 13；trgm 9 索引与 pgvector 向量列维持受控执行（扩展缺失不阻断 migrate），存量库升级专用 RunPython（退役权限点 ×2、版本回填、向量回填）随清库重建退役；前提与重开条件同 ADR-058 D3 |
| [ADR-085](ADR-085-domain-url-prefix-alignment.md) | 已交付 | identity / file / audit / task 四域 URL 前缀与 app 对齐：独立前缀挂载 + 注册串口径（task 拍平 / file 保留）、联动平移 server 380+ / client 395+ 处、新增 `manage.py migrate_api_prefixes` 平移存量库 |
| [ADR-086](ADR-086-screen-free-canvas.md) | 已交付 | 大屏自由画布模式（一期）：栅格之外新增「自由像素布局」——`layout_mode` + `free_layout` 双载荷、允许重叠（层级 = 数组顺序、无显式 z）、上限 60、服务端第二事实源（`normalize_screen_free_layout`）、设计器拆组装层 + 双画布板、`ReResize` 承接手势并补 props↔box 回写与 `snap` 吸附钩子、参考线吸附 + 键盘微调、投屏等比 contain |
| [ADR-087](ADR-087-screen-free-canvas-2.md) | 已交付 | 大屏自由画布二期：编辑效率四项——zoom-to-fit 缩放编辑（`scale` prop + `Ctrl/⌘+滚轮` 指针锚点，zoom 不落库）、多选（组拖动 + 组包围盒）、对齐与分布（6 对齐 + 2 分布）、复制粘贴（内存 + localStorage）、内置 / 我的模板 + JSON 导出 |
| [ADR-088](ADR-088-screen-canvas-toolkit-3.md) | 已交付 | 大屏画布工具集三期：团队模板（`ScreenTemplate` + 复用自由画布校验，三层菜单）、模板导入 JSON、旋转（视觉旋转不进几何）、入场动画（7 值仅展示态）、富文本（受限标记 + 先转义，无 XSS 面）、元素级网格吸附、zoom 键盘档位 |
| [ADR-089](ADR-089-screen-canvas-boundary-followups.md) | 已交付 | 大屏画布边界收回与触发登记（**已交付**）：收回两项「不做」——**动画时序**（`animation_duration` / `animation_delay` / `animation_loop`，只在配了动画时落库、缺省不下发、展示态才写内联样式）与**多元素批量对齐网格**（坐标 + 尺寸取整到步长，一次一个撤销点、已在网格不记点）；其余条目（视频 / 地图 / 自定义 HTML 窗格、跨设备剪贴板、旋转视觉几何命中、富文本扩展、模板治理）登记为**触发制**并写清触发条件与第一动作；多租户维持红线 |
| [ADR-090](ADR-090-screen-canvas-advanced-4.md) | 已交付 | 大屏画布进阶四项（**范围经产品确认，分批交付**）：**富文本扩展**（链接 / 列表 / 图片 / 表格 + 选区工具条与实时预览，仍是先转义再套白名单）、**旋转 OBB 几何**（框选按旋转矩形相交、对齐/吸附按旋转盒 AABB，rotate=0 逐值回归）、**视频与 iframe 窗格**（只收 http(s) 地址，不接受原样 HTML）、**模板目录分组 + 版本历史与回滚 + 跨设备剪贴板**（服务端单槽）| 
| [ADR-091](ADR-091-menu-experience-enhancement.md) | 已交付 | 菜单体验增强：MenuMeta 六字段（高亮路径 / 徽标 / 查询参数 / 分组）全链路、渲染层分组装配、激活项自动滚动、hover 反馈与导航尺寸令牌化 |
| [ADR-092](ADR-092-site-wide-appearance-config.md) | 已交付 | 站点级外观配置全局生效：站点标题 / Logo 品牌位 / 登录页布局 9 键写系统配置（读取以系统值为权威、个人行不沉淀、仅超管可改、缺键自愈），种子补 Title；面板条件项补禁用（混合宽度 / 半暗顶栏 / 页宽回退） |
| [ADR-093](ADR-093-migration-consolidation-and-ledger-claim.md) | 已交付 | 5.0 迁移链合并为初始迁移（结构等价以 pg_dump 归一化比对守护）+ 存量库账本认领（`upgrade_ledger` / `upgrade_check` 新判定） |
| [ADR-094](ADR-094-inplace-upgrade-for-legacy-libraries.md) | 已接受 | 四域切分前的旧库支持**真·原地升级（立项）**：同库结构对齐（表改名 + 补列回填 + 权限/存量串重指 + 账本重写，单事务）替代清库重建 + 回灌；二开迁移图用历史链兼容包（state-only）解决、改写工具兜底；判定 fail-closed + 预演 + 备份回滚；回灌降级为逃生舱 |
| [ADR-095](ADR-095-per-app-seed-layering.md) | 已交付 | 内置种子按应用分层（**已交付**）：平台层 `loadjson/`（目录菜单 / 平台级页面权限点 / 角色 / 配置 / 字典）+ 各应用 `<app>/seeds/`（manifest 自声明 depends / url_prefixes / config_keys / grants，依赖拓扑排序）。归属规则可穷尽（前缀最长匹配 / 页面众数 ≥80% / 字段树根前缀），实测与路由解析口径 0 偏差；角色授权改「声明式 grants + 装载后并集」（破除 loaddata m2m 覆盖式清零）；迁移用 `seed_split` 写盘前逐行等价校验；导入导出共用 `split.py` 唯一实现；生成器直接写本应用 `seeds/` |
| [ADR-096](ADR-096-ai-action-declaration-discovery.md) | 已交付 | AI 动作声明按应用下沉 + 自动发现：各应用 `ai_declarations.py` 声明、注册表合并唯一；内置动作回归业务应用；`modules` 字段对齐功能模块裁剪；明确不按路由自动生成动作 |
| [ADR-097](ADR-097-data-scope-list-anchor.md) | 已交付 | 数据权限「显式 action 覆盖 + 同资源 list 缺省回退」：当前 action 有可编译显式授权时只用它，否则回退 list；通用授权始终参与；字段权限保持精确匹配不继承；配套修正种子旧表名 `system.userrole` → `identity.userrole` 并加模型 label 守护 |
