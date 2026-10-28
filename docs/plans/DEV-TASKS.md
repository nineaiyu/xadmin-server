# xadmin 开发任务总清单（2026-10 起）

> 生成：2026-10-09。由工作区既有开发文档收敛重写——`NEXT-DEV-PLAN.md`（执行记录一~二十二）、
> `CODE-REVIEW-TASKS/`（13 份任务台账 + 对账总表）、两份代码审查报告、`PHASE-C-HANDOFF.md`。
> **上述文档的结论已全部收敛进本清单与仓库内两份台账**（见下方权威源）；原始归档件已随
> 2026-10-14 工作区文档整理清理，追溯历史细节以仓库内台账 / ADR / git 提交为准。
>
> 本清单是**工作区级唯一待办入口**（2026-10-14 由工作区根迁入本目录，纳入版本保存）；
> 同目录两份长期台账是触发制 / 时间窗的权威源，本文摘其要点，两处不一致时以它们为准再回写本文：
>
> - `触发制任务清单-长期.md` —— 触发条件 / 红线重开 / 供应链 / 时间窗 / 滚动债；
> - `下阶段长期计划-2026.10.md` —— 月度窗口排期 / 贯穿专项 / 候选池。
>
> **纪律**（沿用项目规约）：触发条件未命中不立项、不占窗口、不做预备工作；每项独立立项、
> 独立 commit；结构 / 接口变更先 ADR；新功能 100% 带测试；改动后端后跑 `pnpm test:e2e:fresh`；
> 只有用户明确说提交才 commit；改动完成后保留工作区待人工检查（桌面级检查项 = 门禁全绿 + 编号纪律自查）。

---

## 〇、状态快照（2026-10-25 提案候选五批；2026-10-23 及更早批次已滚动归档）

> 2026-10-23 及更早的批次执行记录（审查整改 / 依赖补丁 / 字段类型渲染 / 角色授权审计 /
> 登录链路重构 / 种子分层 / 5.0 发布窗口等）已于 2026-10-27 清理，
> 移入工作区 `_delete/2026-10-27-plans-cleanup/`（可恢复，去向见 `plans/README.md`）。

- **2026-10-28 双篇审查整改批次（全部收口，全量验收已跑）**：P0 + P1 #9~#13、`FORM-P1-05`、
  C 级 41 + B 篇 15；六轮补 WS 4401 语义、列元数据兜底、联动隐藏清理与逐列断言；
  后端全量 / 前端 3023 例 / 全量 E2E 全绿；见 [交接单](交接-审查整改剩余项-2026.10.28.md)。

- **2026-10-26 三件收口**：弹层内核统一（`OverlayDialog`/`OverlayDrawer` + 19 处迁移，基线 21→4）、
  设计器实时预览（未保存 schema 联动、不可提交）、a11y 扩面（扫描 10→12，双浏览器 24 例）；
  vitest 439/2837、check 全量 + 行数门禁绿，弹层相关既有 E2E 32 例过。

- **2026-10-25 提案候选第五批（B-3 外观包 + P1 联动二期 + D-2 工作台 WS 推送，改动已提交
  client `de446dbd` / server `6d4c0f07` + client `cc42d6a1` / server `e7041372` + client `e24371de`，
  两份提案可立项候选至此全部消化）**：
  ①**B-3 站点级外观包导入导出（纯前端零后端，client `de446dbd`）**：偏好面板「更多」新增
  「导出 / 导入外观包」——导出落 JSON 文件（类型信封 + 剔除 `Version` / 存储命名空间构建期键）；
  导入（仅超管，入口菜单门控）校验从严（未知键 / 值形态不符 / 版本过新整体拒绝，不做部分导入），
  合并写回系统配置行 `WEB_SITE_CONFIG`（站点级键唯一权威源，含 Title/Logo 等站点身份键）→
  失效缓存 → 重同步站点配置 → 重挂载面板组件；个人已自定义偏好仍以个人值为准（默认值迁移
  而非强制下发）。vitest 11 例（round-trip / 构建期键剔除 / 校验拒绝 / 合并语义）。
  ②**P1 联动二期（卡片间联动过滤，server `6d4c0f07` / client `cc42d6a1`）**：后端 aggregate
  端点接受请求级 `filters`（`validate_filters` 口径同 execute，线程穿透 `aggregate_dataset →
  build_queryset`，大屏 / 报表既有调用不受影响）；前端 `useDashboardLinkage` 状态机——带 raw
  的分组点击设置 / 切换联动（来源卡排除、数据集缺维度字段静默跳过、趋势 / 无分组点击不改变
  联动），`ChartCard` 新增 `linkageFilter` prop 随取数下发，联动 chip 展示来源卡与维度值可
  一键清除（kiosk 展板亦可见），编辑态 / 切盘复位；点击行为 = 下钻抽屉 + 联动伴生。集成测试
  3 例 + vitest 11 例 + E2E 联动用例。
  ③**D-2 看板 WS 数据推送·工作台半边（server `e7041372` / client `e24371de`；大屏半边已在位）**：
  新通道 `ws/dashboard/<pk>`（`DashboardDataNotify`：连接即自推首帧、准入与 HTTP 可见性同口径
  超管 / 创建者 / shared、合并窗口合并背靠背触发）+ `dataset/dashboard_data.py` 按观察者聚合
  整盘（复用大屏取数内核 execute_card / load_datasets 改公开名，卡片级 allowed_roles 过滤、
  同帧同参数去重、单卡失败进 errors 不中断——数据权限 fail-closed 与逐卡 HTTP 重拉等价）+
  beat 任务 `push_dashboard_data`（15s，离盘不占位不落键、`cache.add` 原子占位）+ 协议新增
  `dashboard_data` 帧与 `DashboardDataPayload`（ws-frame.schema 再生成，双端同步）；前端自动
  刷新新增「实时（推送）」档（-1）——`useDashboardChannel` 开合连接（切盘重连、编辑态不吃
  推送）、帧按卡片 id 路由 `ChartCard.applyData`（与令牌重拉共用渲染路径）。集成测试 25 例 +
  vitest 8 例 + E2E 实时档用例（断言 ws 连接建立与首帧到达）。
  **同批门禁清偿**：`check:hook-length` 越线 4 处——`usePanelActions` 拆出
  `useAppearancePackActions`、`useScreenExport` 下沉 `screenExportFiles`、两处注释归并回落。
  **验证**：后端全量 `PYTEST_EXIT=0`（真 PG / Redis，pipefail 取真实退出码）；ruff、行数 / 函数 /
  缓存键 / 菜单标题 / 台账编号门禁、`makemigrations --check`、`dump_openapi --check`、
  `gen_ws_frame_schema --check` 全绿；前端 vitest 425 文件 / 2781 例、typecheck / lint /
  `check:*` 全量 15 项全绿；dashboard E2E fresh 双浏览器全过（含联动 + 实时推送新例）；
  容器重启 + 容器内冒烟：ws/dashboard 路由注册、beat 注册（interval=15 / module=analysis）、
  带鉴权 WS 连接收 `dashboard_data` 首帧（4 卡 0 错误）、匿名连接被拒、aggregate filters 生效；
  真机浏览器（vite dev + 开发库）实测：实时档选择 → 父子模型同步 → ws/dashboard 建连（探针
  记录 URL）、选择器正确回显「实时（推送）」。
  **未做（维持提案归位）**：A-2/B-2/C-1/C-2/D-3/E-1 等触发制项、移动端 / print.ts / RePlusPage
  包化（触发制）、P8 PDF 快照二期（再议）、红线项不触碰。

- **2026-10-25 提案候选第四批（P2 AI 日报 + P4-2 AI 字段草案 + P5 MCP 治理 + B-1 组件 Playground，均默认关/纯前端增量）**：
  ①**P2 AI 主动日报**：`ai/services/daily_report.py` 只读聚合（审批待办 / 监控 firing /
  24h 异常请求 / 任务失败 / AI 用量——单指标失败按 0 兜底不拖垮整份）→ `structured_chat_client +
  tracked_chat`（feature=新增 `daily`，不落会话表）→ 缓存 48h；周期任务 `ai_daily_report_job`
  （07:31，module=ai，开关关时空转）；端点 `GET /api/ai/assistant/daily-report`（指标实时聚合 +
  摘要走缓存，请求路径零同步 LLM；权限点并入 status 正则）+ welcome 新卡（指标芯片异常着色、
  点击直达对应页面，fetcher 静默降级、无权限不发请求）；开关 `AI_DAILY_REPORT_ENABLED` 默认关。
  ②**P4 第二阶段 AI 字段草案**：`devtools/services/codegen/ai_draft.py`——自然语言 →
  `CodegenFieldOverride[]` 草案，与 GUI 字段表格同套约束逐项过滤（未知字段/非法 input_type/
  字典绑定与字段类互斥等非法项剥成 warnings 而非整体拒绝，pk 不可排除）；模型字段目录经
  `annotate_reference` 注入防护；端点 `POST /api/system/codegen/ai-draft`（权限点
  `aiDraft:SystemCodeGen`）+ 前端两段式人审弹窗（逐项勾选采纳 → 合并进字段表走既有
  preview/download 管线，AI 不直写代码）；`AiUsageRecord.Feature` 增 `daily`/`codegen`（合并迁移
  0004）；开关 `AI_CODEGEN_DRAFT_ENABLED` 默认关。
  ③**P5 MCP 工具治理**：服务器级日配额 `McpServer.daily_call_limit`（0=不限，迁移 0005）——
  计数源=审计流水（`OperationLog` module=AI:mcp:client 当日行数，拒绝行同样计入），
  `daily_quota_error` 收敛两处拦截（管理面 call + AI 动作链路 McpActionSpec.execute，TOCTOU
  后白名单后置）；审计报表 `McpCallLogViewSet`（预过滤 module，只读+详情+导出、无删除端点，
  server/tool/channel/result 从 changes JSON 提取，权限面独立 `list/retrieve/exportAsync/
  exportData:McpCallLog` 挂 AiMcpServers 页）+ 行级「调用记录」抽屉（结果过滤/分页/渠道翻译）。
  ④**B-1 组件 Playground**：静态路由组 `/playground`（showLink=false，二开者从手册进入，
  无需权限点、不撞 entry-closure/bundle-size）+ 43 组件注册表 `registry.ts`（分组/用途一句话/
  样例归属）+ 索引页（分组浏览+搜索）+ 三张 demo 页（展示/布局/输入，16 组件活样例 + props
  速查取自各组件真实类型定义）；`src/components/README.md` 漂移修复（7 组件补登记、删已不存在的
  ReAuth）；vitest 守护：注册表 ↔ README 分组表名字集合一致、demo 页引用组件全部已注册。
  **同批框架修复**：`get_view_permissions` 对纯 `viewsets.ViewSet + @action`（无 queryset 的
  非 GenericViewSet，如代码生成器）动态路由 mapping 由 get_urls 期才装配——新增「URL 名→动作
  函数名」分支，动作标题取自 action docstring（此前回退成视图名字符串，会被菜单 i18n 守护拦下），
  回归测试落 `test_menu_utils`；连带发现上一批遗留 `settings→notifications.backends` 模块级
  import 违例，改函数级惰性导入（`check_cross_app_imports` 复绿）。
  **验证**：后端全量 PYTEST_EXIT=0（7014 passed / 2 skipped，真实退出码）；ruff format/check、
  mypy 1001 文件 0 错、脚本门禁 11 件、`makemigrations --check` 无漂移、`load_init_json --check`
  0 error；E2E fresh（dashboard 4 + ai 5 + mcp 2）11 例全过；前端 vitest 422 文件 / 2751 例、
  typecheck/lint/i18n/contract 等 check:* 11 项全绿；容器重启 + 容器内实测三端点（超管 200 /
  未授权角色 403 / 开关关只回 enabled=false）；种子基线 菜单 724→729、字段 2134→2141、
  权限点 648→653（新增 5 权限点：aiDraft + McpCallLog×4，无法归属 0）。
- **2026-10-25 第三批：T4 仪表盘编辑态拆分 + T11 生成器同源收口（client `2d10b12d` / server 本批）**：
  ①**T4（client `2d10b12d`）**：`DashboardToolbar`（工具栏整体：选择器 + 编辑/浏览按钮组 +
  自动刷新 v-model）与 `CardHeaderActions`（卡片头按钮组按 editing 分支）组件化，下钻状态机
  下沉 `useCardDrill`；index.vue 430 → 273 行纯编排层；拖拽手柄留页面卡片头（sortable handle
  选择器定位不变）、data-testid 全保持——行为零变更。验证：typecheck/lint/vitest dashboard
  34 例 + E2E dashboard 4 例（主链路含编辑态/快照/下钻/聚合配置）全过。
  ②**T11（P4 第一阶段）**：调查结论 = **模板完全同源（单一事实源）**——GUI 无自有模板，
  是 CLI 引擎（`_build_context → _collect_artifacts`）之上的只读适配层，中间表示（ctx）与
  渲染管线 100% 共享，git 历史呈同步演进、无「改一边忘一边」；无需「统一模板源」改造。
  收口两件：**CLI 补 `--field-class` 旗标**（专用字段类此前仅 GUI 可达）——推导单源化到引擎
  `analysis.apply_field_classes`（模块级函数，CLI 旗标与 GUI 表单共用，GUI fields.py 改调引擎
  并消掉内嵌推导块）；**同源守护测试**两条（`TestSameSourceAsCli`）——零覆盖基线产物逐字符
  相等（守护模板与 options 组装不漂移）+ 专用字段类声明行与 CLI 旗标等价（守护推导同源；
  GUI 表单序重排为其特性，不断言全文件逐字符）。
  验证：生成器 120 例（含 CLI 旗标 5 例新用例）全绿、ruff/mypy（devtools）绿。
  **P4 第二阶段（AI 字段草案）未启动**——涉及 LLM 调用成本与 AI 域新接线，建议独立批次。
  **同批修复（第二批遗留）**：①五个种子守护红灯（FORWARD_KEYS 未登记 WEBHOOK 三键 /
  declarations 奇偶清单 / 管理员缺 `retrieve:WebhookNotifySetting` 授权 / 种子基线
  菜单 721→724·字段 2095→2134·权限点 645→648 / `WEBHOOK_SECRET` 未登记
  ENCRYPTED_SETTING_KEYS）——全部为第二批 webhook 权限点与配置键的配套面漏跑守护；
  `settings/seeds/grants.json` admin 补 1 项声明、`test_seed_grants` 基线 admin 283→284。
  ②**mypy 6 错既有债修复**（stash 对照证明系 10-23 审查批次 `9db884f2` 引入、与本轮无关，
  但挡全仓门禁）：`contract.py` 混入改继承 Serializer（运行期 MRO 线性，类型面可校验）+
  `services.py` 4 处懒代理返回按既有 `str(_())` 口径收敛；mypy 997 文件 0 错。
  **最终验证（真实退出码口径）**：后端全量 PYTEST_EXIT=0；ruff format/check、
  mypy 全仓、种子守护五件套全绿。
- **2026-10-25 提案候选第二批（P7 收尾两件：@提及联想 + webhook 渠道，已提交 server `83d9cda0` / client `413b3392`）**：
  ①**P7② 审批讨论 @提及（纯前端，后端解析/通知既有）**：`InstanceDetail` 收集参与人
  （申请人/各节点处理人/代理人/实际处理者/抄送人，label 即 username）传入讨论区；`useMentionSuggest`
  （末尾 `@片段` 触发、包含过滤、插入补空格终结——正则与后端 `MENTION_PATTERN` 同口径）；
  评论正文按 `@用户名` 分段高亮（文本节点直出不走 v-html）；候选排除自己（与后端 @自己不提醒对齐）。
  ②**P7③ Webhook 通知渠道（站点级转发端点）**：`BACKEND` 枚举 + `ChannelSpec` + `backends/webhook.py`
  （消息与目标用户整体 POST，签名与开放平台事件投递同口径 `sha256=HMAC("{ts}."+body)` +
  `X-Webhook-Signature/Timestamp` 头；出站走 `pinned_request` 统一出口，私网默认拒绝、白名单放行；
  收件人截断 200；失败吞掉不阻断其他渠道）；配置面 `notify/webhook` ViewSet（category=notify_webhook，
  secret 值级加密）+ 消息设置页新页签（复用 settings-shell）；「开而 URL 缺」自动降级不可用；
  权限点 3 个经 `parent_menu_map`（settings/declarations.py）归属 SettingMessage，`sync_menu_permissions
  --dry-run` 缺口 0。
  **验证**：前端 vitest 418 文件 / 2730 例、typecheck/lint/i18n/contract 门禁全绿；
  ruff、脚本门禁、`load_init_json --check`（0 error）绿；E2E approval-depth 4 例 + 设置/订阅面 5 例过；
  容器重启 + 冒烟：渠道注册表加载、默认降级、retrieve/patch 热更生效、订阅渠道列表含 webhook（配置后）；
  冒烟假配置已清理并重启容器。zh 译文按准则手工补 po + compilemessages（不跑 makemessages）。
  **纠正（2026-10-25 晚）**：本批「后端全量 exit 0」结论有误——管道 tail 掩盖了 pytest 退出码，
  实际 4 个种子守护红（FORWARD_KEYS 未登记 3 键 / declarations 奇偶 / 管理员缺
  retrieve:WebhookNotifySetting 授权 / 种子基线 721→724·2095→2134·645→648），同日在第三批修复
  （follow-up commit）并复跑全量取证。教训：**全量判定必须落退出码（`echo PYTEST_EXIT=$?`），
  不能以管道尾部输出代判**；新增权限点后须跑的守护是 `test_seed_plan` / `test_app_declarations` /
  `test_builtin_role_readonly_coverage` / `test_settings_forwarding` / `test_seed_grants` 五件套。
- **2026-10-25 提案候选首批交付（改动已提交 server `57e4d8b0` / client `27772388`，来源=两份 2026.10 提案近期窗口小件 + 前端 Top3）**：
  ①**P1 仪表盘卡片下钻（单向下钻，联动二期）**：聚合序列带 `raw` 原始分组值、`execute_dataset`
  增 `extra_filters`（下钻「按维度值看明细」，视图层先 `validate_filters`）；前端 `CardDrillDrawer`
  复用数据集预览表格，可清过滤回全量；顺带聚合卡片度量字段（sum）可配置。T4（dashboard 拆分）
  未随本批做（`index.vue` 为增量而非拆分，拆分另批）。
  ②**P3 增量（ADR-028 §7）**：分组带 `search_field`，命中跳转 `?<field>=<关键词>` 经 RePlusPage
  route.query 装配直滤到命中（对齐面由 `test_search_field_aligned_with_list_page_filter` 钉住）；
  `register_search_provider` 二开注册扩展点（key 冲突 fail-fast，两道权限门与内置一致）。
  ③**P6 全局密度偏好**：`UiSize`（default/large/small）入 systemconfig 种子 + 偏好面板 + 表格
  size 全局注入（`useTablePrefs`/`tablePrefsApply`），与内容区紧凑模式分层（非 padding 口径）。
  ④**P7①系统消息订阅化基建**：`SystemMessage.user_subscribable` 标志 + 收件人按渠道集合并集分组
  （系统订阅者 ∪ 自选订阅者，站内信恒兜底）；registry 增 `user_subscribable` 键，视图层合并展示；
  阈值类消息开放自选（monitor 侧），②@提及 / ③webhook 渠道未做。
  ⑤**P8 仪表盘/大屏快照导出（纯前端）**：`snapshotComposite.ts`（逐卡 `getDataURL` 合成 + 标题/
  时间戳/水印叠加）+ `useDashboardSnapshot`；大屏 `useScreenExport` 改走同一合成链，非图表窗格
  首版降级说明；落下载中心，零新端点零新依赖。
  ⑥**A-1 命令面板「问 AI」**：⌘K 面板新增「问 AI」入口，经 `?q=` 直达 AI 控制台问答并预填
  输入框（`initial-draft` seq 保重复触发；消费后 URL 移除防刷新重复预填）。
  ⑦**E-2 axe-core 无障碍门禁**：`test:e2e:a11y` 定向档（wcag2a/2aa，只阻断 critical/serious，
  豁免表 `ALLOWED_VIOLATIONS` 只减不增、扩白须先登记 `e2e/README.md` / `ALLOWED_VIOLATIONS`）；基线 3 页 +
  扩面 4 页（流程配置抽屉 / 菜单树行 / welcome / AI 控制台），spec 随全量档执行。
  **验证**：后端全量 pytest exit 0（真 PG/Redis，`-n auto`）；前端 vitest 417 文件 / 2717 例、
  typecheck、lint、`check:i18n/contract/ledger-ids/version` 全绿；ruff format+check、行数/函数/
  缓存键/菜单标题门禁、`makemigrations --check`、`load_init_json --check`（0 error）全绿；
  E2E passed（dashboard 下钻 + 快照导出 2 新例、a11y 7 例、命令面板问 AI、全局搜索命中跳转）；
  容器重启 + 开发库复灌后冒烟 4 项通过（global-search API `search_field=username` 生效、
  `UiSize=default` 落库、注册表含 `user_subscribable`、`register_search_provider` 可调用）。
  **未做（维持提案归位）**：P2 AI 日报（观察窗口）、P4 生成器合流（中期，先 T11）、P5 MCP 治理
  （观察窗口）、P7②③、T4 拆分。

- **2026-10-23 审查整改批次（442 条全验收，server `24d01e98` / client `c9490c53` / installer `3f45a34`）、2026-10-23 依赖安全补丁、2026-10-20 字段类型全量自动渲染、2026-10-19 内置角色授权审计 / 登录链路重构 / AI 动作声明下沉、2026-10-18 种子分层收口、2026-10-17 清库重建升级演练、2026-10-16 5.0 发布收口、2026-10-14 5.0 发布与授权修复各批**：执行记录已于 2026-10-27 清理（移入工作区 `_delete/2026-10-27-plans-cleanup/`，可恢复）。
## 〇-历史

> 2026-10-23 及更早的历史执行记录（2026-09 审查整改收口、2026-10 各批交付与推送明细）
> 已于 2026-10-27 清理（移入工作区 `_delete/2026-10-27-plans-cleanup/`，可恢复）。

## 一、近期待办（2026-10 ~ 2026-11）

| # | 事项 | 验收 / 触发口径 |
|---|---|---|
| 1 | ~~工作区改动复核入库~~ | **已完成（2026-10-09）**：server `35d8e0dc` / `45c3c8a8` + installer `3348910` 入库并推送；门禁全量复验通过 |
| 2 | ~~F6 容器化测试验收终判~~ | **已完成（2026-10-09）：通过（达标）**——时长 ✅ + 9 个红 run 全部分诊收敛（零真库基建故障）；字面「连续 2 周绿」余窗顺延至 10-21 观察；处置方案（回退 sqlite）不适用 |
| 3 | ~~T5 Renovate 验收~~ | **已完成（2026-10-16）**：修 `renovatebot/github-action@v41` → `v46.3.8`（该 action 无 major tag，此前 workflow 从未成功运行过），两仓 `gh workflow run renovate.yml` 均 success，且已实际开出 PR（`renovate/pypi-pyjwt-vulnerability`） |
| 4 | T6 SLO 校准 | 数据成熟（满 3 个月）即校准回填 `docs/metrics.md` |
| 5 | ~~5.0 端到端升级演练（4.2.5 实例 → 5.0）~~ | **已完成（2026-10-17）**：虚拟机以 4.2.5 库跑九项验收全通过（导出 → 清库重建 → 回灌 → 计数/关联/附件/旧密码/幂等 → `upgrade_check`=up-to-date + health 四项 true）。**结论 = 切分前旧库原地升级不可行**，当时路径为清库重建 + 回灌（手册 `docs/ops/upgrade-4x-to-5x.md`）；方向已由 #8 改写。**同轮修复升级通道三处缺陷**（installer：镜像 tag 带 `v` → `manifest unknown`、备份开关缺 `${VAR:-}` 缺省、基线门禁误判）+ PG 大版本数据目录迁移。 |
| 6 | ~~文档站死链与口径守护补强~~ | **已完成（2026-10-17）**：新增 `scripts/check_docs_site_links.py`（锚点级 + 跨仓路径引用校验，进 docs CI），抓出并修复 8 处锚点死链；升级门槛串纳入事实门禁（+3 条）。 |
| 7 | ~~回灌链路端到端集成测试~~ | **已取消（2026-10-17，用户决策）**：回灌路径对二开损失面过大（旧迁移引用须人工改写、表文件不搬、审批/表单/数据集/日志/任务/PAT 不在搬运面内），转 #8；回灌保留为逃生舱。 |
| 8 | **真·原地升级立项与分批实施** | **已立项（2026-10-17）**：决策 [ADR-094](../adr/ADR-094-inplace-upgrade-for-legacy-libraries.md) + 方案 [原地升级立项与方案-2026.10.md](原地升级立项与方案-2026.10.md)。**第一~三批已交付**：旧结构样本生成器 + 结构与引用与账本一键对齐（`legacy_align`）+ 差异报告 + 体检新判定 `legacy-inplace-capable` + 安装器接线（`--probe` 三态）+ 二开 app 迁移引用改写工具；`v4.1.3`/`v4.2.3`/`v4.2.5` 三代样本原地对齐全绿（实测数据见方案 §八）。**第四批（实机演练完成）**：真实 4.2.5 库升 5.0.1 跑通、数据零丢失（行数+哈希比对）；修复「容器无 git → common_monitor 未映射」与「有菜单无列（字段白名单 fail-closed）」两个真问题（后者改 post_upgrade 自动补齐+清缓存）。 |
| 9 | ~~应用级种子拆分（五批次）~~ | **已交付（2026-10-17）**：种子按 app 落 `<app>/seeds/`（平台层保留 `loadjson/`），12 个 manifest 自声明依赖与归属；`load_init_json --list/--check/--apps` + `seed_split` + `dump_init_json --split-apps`；角色授权改声明式 grants 并集（破除 loaddata m2m 覆盖清零）；决策 [ADR-095](../adr/ADR-095-per-app-seed-layering.md)，执行记录见 [执行计划](应用级种子拆分执行计划-2026.10.md) |
| 10 | ~~前端框架一致性收敛（表单字段元数据化 + 弹窗全屏按钮体验缺口）~~ | **已完成（2026-10-18）**：调查、迁移与验证证据见 方案（历史文档已清理） §九。① ReDialog 全屏按钮默认渲染；② `check-handwritten-forms.mjs` 门禁 + 豁免登记进 CI；③ post / tag / login-policy / subscription / mcp / api-app / screen / approval-rule 八页迁至元数据表单（混合形态用字段渲染器或扩展字段）；④ 选型判定写入 component-handbook §六；⑤ 顺带修 `headerRenderer` DOM 泄漏、`time` 协议、`tag.color` 类型、MCP `pk` 只读、`formProps.rules` 整块覆盖；⑥ 最大化态强制显示还原按钮（`lay-tag/index.vue`，避免进入最大化后无可见出口）。数据集/报表因联动列维持豁免。验证：vitest 2312、后端 103 passed、E2E 双浏览器 60+（含 `tag-bar` 最大化还原 6 例） |
| 11 | **下一补丁版本发布（含审查整改批次）** | 版本号三方同步（`server/const.py` ↔ `client/package.json` ↔ tag）→ 变更日志/Release notes → 按 server·client → web → docs → installer 顺序构建镜像 → `sync-image-mirror` → 目标机复测；发布前跑一次干净机安装 + 幂等升级 |
| 12 | **4.2.5 旧镜像回填 + 目标机旧库升级复跑** | **已登记（2026-10-23）**：`postgres:16.8` / `redis:7.4.3` 已入 `xadmin-installer/utils/base-images.yml`；待 push 触发 `sync-base-image-mirror` + `gh workflow run sync-image-mirror.yml -f version=4.2.5` 补 `xadmin-web`，再目标机 `./xadmin.sh upgrade v<version>` 复跑，验收 `upgrade_check=up-to-date`、健康全 true、数据保留 |
| 13 | ~~R5 首屏 LCP 靶心优化~~ | **✅ 已完成（2026-10-23）**：welcome 生产口径 LCP **792 → 244 ms（-69%）**、CLS 0.0095 → 0.0088–0.0095、FCP/TBT 持平；改动 = 移除欢迎卡片入场动画 + 活跃用户卡预占位；证据见 `xadmin-client/docs/perf-firstscreen.md` §六 |
| 14 | **双篇审查整改批次（2026-10-28，代码修复完成，运行时验收待跑）** | P0 5 → B 篇立即/次周 → A 篇 P1 47 编号（含版本化 2 项补测试）→ B 篇 P1 34；月度/结构性 16 项顺延，P2 backlog 按域归组。状态见 [审查整改批次-2026.10.28.md](审查整改批次-2026.10.28.md)；验收口径 = 每项带测试 + 门禁全绿 + 后端改动 E2E fresh |

> 2026-11 窗口两项主项（存量库升级通道、HNSW 向量定型实测）已于 2026-10-09 提前完成，无需再排。

---

## 二、月度窗口排期（滚动，1 主项 / 窗口）

| 窗口 | 主项 | 说明 / 验收口径 |
|---|---|---|
| 2026-10 | 审查整改收口 + 下一补丁发布 | **审查批次已提交（2026-10-23）**；主项为下一补丁版本发布与 4.2.5 旧镜像回填；R5 首屏 LCP 作为工具类副项提前启动 |
| 2026-12 | 依赖窗口 | D2 TS 7.1 重评 / D3 Node 26 转正 / O9-6 停维依赖季度复核；独立分支 + 全量门禁 |
| 2027-01 | mypy 严格度提升试点 | **✅ 内核 2026-10-09 完成**（9 flag 子集；**不可写 `strict = true`**——mypy 2.x per-module strict 会全局生效；守护测试 `tests/unit/test_mypy_strict_pilot.py`）；**✅ 业务 app 全量收口（2026-10-09）**：第一批 7 个（captcha/demo/devtools/integrations/mfa/server/settings，722 处）+ 第二批 11 个（audit/file/demo_seed/notifications/task/message/dataset/approval/ai/system/identity，4092 处）存量告警清零入列；全仓 mypy **860 文件 0 错**（无未推广面，`PROMOTED_APPS` 与配置块双向校验） |
| 2027-01 | 基线清偿批次一 | **✅ 2026-10-09 提前完成**：新增 `scripts/check_function_length.py`（AST ≥100 行即失败，基线只减不增；CI + PR 模板 + 6 例自测）；函数净减 7 → 2（余 113/111 登记基线）。**2026-10-23 复核：超阈值函数 0 个、巨型文件 0 个，基线正式清零** |
| 2027-02 | 二开实战验证 | **✅ 2026-10-09 提前完成**：示例分发包 `examples/plugins/xadmin-demo-plugin`（entry point + `ready()` 双路径、路由 / WS / 周期任务 / 自有迁移 / 模块声明）+ 教程 `docs/guide/plugin-development.md`；真机 `pip install` 与 `XADMIN_APPS` 注册双进程实证 + 停用三层拦截实证，11 例守卫测试 |
| 2027-03 | 备份恢复季度演练 + 半年复盘 | 按既有演练脚本口径执行，回填 `docs/ops/backup-drill-*`；对账本计划半年执行度 |

> 排期原则：按需插槽制，连续两个窗口未启动的主项降级回候选池并说明原因。

---

## 三、触发制任务（⏸ 条件未到不立项、不占窗口、不做预备工作）

1. **TG-1 JSONField 慢查询逐一物化索引**（唯一开放中的开发类触发项）
   - 触发条件：生产出现真实慢查询且定位到具体 JSON 字段查询路径；
   - 2026-10-09 复核：全量盘点（模型层 64 字段 / 11 个查询点）+ 20 万 / 200 万行真实 PG 实测 ——
     筛选链路 `filter_data @>` GIN 命中正常；dataset 键提取不可静态索引；webhook / `extra` 属受控小表低频面；
     **零新增索引**，补 `filter_data` GIN 守护 5 例。触发条件不变，维持待命。

2. **红线（🚫 明确不做，命中重开条件才重评）**

| 项 | 重开条件 | 重开后第一动作 |
|---|---|---|
| 多租户 | SaaS 商业化诉求 | 重走 ADR（数据隔离读 / 写 / 巡检三面已有） |
| 独立移动 APP / uni-app | 原生能力 / 商店分发 / 移动占比 >40% | 先实测移动端数据再立项 |
| 工作流 BPMN 化 | 复杂编排诉求 | 评估自研引擎扩展 vs 引擎替换 |
| 在线建表（运行期 DDL） | 无既定条件（明确不投） | 需产品层重新决策 |
| AI 成本计价 | 结算诉求 / 云支出限额 / 分发场景任一 | 预研已固化：定价表 → 记录级成本快照 → 汇总展示三层 |
| SAML / CAS SSO | 企业客户需求 | 走开放平台 OAuth2 既有扩展点评估 |
| AI 多供应商原生适配 | 真实客户需求 | 先核实 OpenAI 兼容层覆盖缺口 |
| common 独立包化（RePlusPage 前端半边） | 多个前端仓复用组件库的真实需求 | 后端半边已交付（uv 工作区 + settings 契约 + 发布渠道）；前端触发复核未命中维持待命，命中后走 pnpm workspace 源码包路径 |
| pgbouncer / `fields` 端点内继续优化 | 无（连接池债已根治、无单点热点） | 不重开；容量问题走 worker 档位 |

3. **供应链与安全登记（周期复核）**
   - **O9-6 停维依赖季度台账**：`unicodecsv` / `django-ranged-response` / `user-agents` / `ldap3` / `pilkit`
     逐包复核（`pyexcel` 已核实仍活跃移出清单）；处置路径与重开条件见 `asvs-mapping.md` 七期 S-2；
   - **O9-7 wangEditor 替换触发三条件**：安全公告无上游修复 / 协同编辑等产品需求 / Vue 主版本致适配层不可用；
   - **O8-7 CSP Django 侧 enforce 重开条件**：swagger enforce 兼容性完成一轮观察，或出现绕过 nginx 直连 Django 的 HTML 面；
   - **其他已接受安全项**：JWT 存 JS 可读 Cookie、HTTP 直连无 Secure、注册账号占用提示、Django 6.0.8 EOL 监控等，
     理由与重开条件见 `asvs-mapping.md` S-6 台账（季度窗口随 S-6 一并复核）。

---

## 四、时间窗（🕐 到点才动，到期未触发先复核窗口判断）

| 编号 | 组件 | 当前 → 目标 | 触发 / 阻断物 |
|---|---|---|---|
| D1 | Django | 6.0.8 → 6.2 LTS | 唯一阻断：`django-celery-beat 2.9.0` 声明 `Django<6.1`；声明放宽或 6.2 全家桶覆盖即动 |
| D2 | TypeScript | 6.0.3 → 7.1 后重评 | 预计 2026-12（受 vue-tsc 阻断）；准备项已交付（`typecheck:tsc` / `typecheck:vue` 拆分） |
| D3 | Node | 24 → 26 LTS | 2026-Q4 转正后（晋升一般在 10 月下旬）；`.nvmrc` / `Dockerfile-base` / `engines` 三处同步。**2026-10-19 复核：未到点**——本机 nvm 最新 v24.20.0、无 v26；等 LTS 公告后动 |
| D4 | Vue | 3.5 → 3.6 stable | stable 发布后；Vapor opt-in，约 0.5 人日 |
| D5 | 跟踪项 | Celery 6 / Python 3.15 | 2027-12 / 3.15.1+；触发即评估 |

---

## 五、贯穿专项与滚动债

**贯穿专项（随改动面持续，非触发制）**

1. **测试隔离治理**：`transaction=True` 用例的 DDL 不随 TRUNCATE 回滚（schema 跨用例泄漏）——
   为涉及 DDL 的用例建立 schema 重置约定；排查剩余 module 作用域提交型 fixture
   （原待核 `tests/unit/common/test_media_serve.py` 已闭环：fixture 均函数作用域、无跨用例状态，非治理对象）。
   **2026-10-09 收口三例**（同家族：进程级状态 / 共享目录跨用例残留）：①用例内定义模型会注册进 app
   registry（`test_auto_clean_file.py::PlainFileModel`）→ 同 worker 后续 `upgrade_check` 体检按注册表
   列举期望表时误报缺表 → finally 内 `apps.all_models.pop` + `clear_cache()`；②存储缓存清理用例按绝对
   计数断言（`test_storage_backend.py::test_clean_storage_cache`）→ 同 worker MEDIA_ROOT 跨运行复用带出
   历史遗留文件 → 改断言语义（目标文件被清 + 至少 1 个），不再断言精确计数；③`SwitchableStorage` 委托
   缓存指纹纳入 `MEDIA_ROOT` / `MEDIA_URL`（原指纹只含 SysConfig 键，同 worker 内改 MEDIA_ROOT 的用例
   落盘到旧目录 → 新增 `delegate_fingerprint` 作 `_current()` 重建判据，守护 2 例）。
   单例红登记：`test_oauth_flavors::TestCallbackIntegration::test_auto_create_and_unique_binding`
   回调 1006 非确定性（隔离复跑 30 passed），按「真容器 xdist 计时 / 状态敏感抖动」家族登记，
   不做 skip；频次上升再定位缓存键 / TTL 竞争。
   2026-10-09 mypy 推广轮新登记两例（同家族，均非本轮改动引入）——两例均已于当日收口：
   ①`tests/integration/plugins/test_plugin_demo.py::TestModuleDeclaration::test_release_preset_report_and_snippet_include_plugin`
   在 `pytest tests/integration -n auto` 下确定性失败（`MODULE_ENABLE 中存在未知模块：demo_plugin`）——**2026-10-09 已收口**：
   根因 = `plugin_installed_as_app` fixture 注入插件 AppConfig 后未清模块清单 lru 缓存（`discovered_modules` / `all_module_specs` / `module_index`），
   该用例又无 `module_config` 顺带清理 → 命中同 worker 其他测试构建的陈旧清单；修法 = fixture 注入后与还原后各 `reset_module_state()`
   （`plugin_importable` module teardown 同步补一次）；复现 = 最小组合「先构建无插件缓存再跑该用例」（复现文件已移入 `_delete/`），
   修复后 `tests/integration -n auto` 全量绿；
   ②`tests/unit/common/test_auto_clean_file.py::TestOwnFileCleanup::test_soft_delete_keeps_file_hard_delete_removes`
   与 `tests/unit/system/test_upload_preview.py` 同批运行时失败（隔离 / 两两组合全绿）——**2026-10-09 已收口**：
   根因是 `SwitchableStorage` 委托缓存指纹（`config_fingerprint`）不含 `MEDIA_ROOT`，`media_root` fixture 改根目录后
   委托实例不重建、后续用例落盘到旧目录；修复 = 新增 `delegate_fingerprint`（SysConfig 键 + `MEDIA_ROOT` / `MEDIA_URL`）
   作 `_current()` 重建判据（运行期改 MEDIA_ROOT 亦生效），守护 2 例；`tests/unit -n auto` 全量与集成批绿。
2. **会话级锁统一封装**：`pg_advisory_lock` 全仓仅 `ai_vector_ddl` 一处（savepoint 口径已修复）；
   新增锁必须走统一 helper + 守护用例，禁止裸 `finally` 解锁。
3. **滚动债（只减不增，改到哪清到哪）**：

| 项 | 当前基线 | 守护 |
|---|---|---|
| 宽泛异常吞噬（`except Exception`） | 余量只减不增（存量已治理 36+ 处） | 随改动面清理 + code review |
| 前端 `as unknown as` | **0 处（2026-10-11 清零）** | `scripts/check-as-unknown.mjs` |
| 前端超长 hook | **基线清零（2026-10-11）**：≥240 行 0 个、≥120 行 0 个（四个超长 hook 已按域拆分） | 行数门禁 |
| 后端超长函数 | **基线清零（2026-10-11）**：`scripts/check_function_length.py`（AST 计行，≥100 行即失败、基线只减不增）；两存量项（113 / 111）已按产物族 / 风险族拆分达标，超阈值函数 0 个 | `scripts/check_function_length.py` |
| 后端巨型文件（>500 行） | 存量基线 0 项 | `scripts/check_file_length.py` |

---

## 六、候选池（启动前需产品优先级确认）

> 保留项结论见 [保留任务决策](保留任务决策-2026.10.md)。

| 项 | 说明 / 前置条件 |
|---|---|
| 适老 / 可读性轻方案 | 用户级字号设置或浏览器缩放引导（不动全局样式；全局主题方案已否决） |
| print.ts 前端打印 | 零依赖，待有打印需求时引入 |
| 二开脚手架复评 | 2026-10-09 现状更新：示例分发包 + 教程章节已交付（2027-02 窗口），复评条件收窄为「出现多人协作诉求」 |
| 移动端填报 / 审批 | 触发条件：移动端访问占比超阈值（15%~20% 区间届时取值）或出现外勤 / 巡检 / 车间审批真实诉求；命中后最小切片 = 审批待办 + 一张 dform 表单提交的 H5 渲染 PoC |
| ~~两份 2026.10 新功能提案（P1~P8 / A~E）~~ | **已全部消化（2026-10-25 五批交付，见状态快照）**：可立项候选 P1~P8 / A-1 / B-1 / B-3 / D-2 / E-2 及 T4 / T11 前置全部交付，P1 联动二期随第五批补齐；剩余项均属触发制 / 不立项清单（见两提案 §四 / §五），按各自触发条件待命 |

> 2026-10-09 核销两项：**ASGI 异步中间件链迁移**（ADR-078 `33b941e1`，2026-10-02 交付：
> 4 项双模化 + 3 显式同步边界 + 收口复核与链能力守护 2 例）；**缓存与索引季度复核**
> （`check_cache_keys.py --strict` 退出码 0、缓存键/索引季度复核完成（原审计文档已清理））。

---

## 七、历史审查遗留登记（维持评估，防重复立项）

> 来源：两轮代码审查的处置结论，原台账（对账总表 / 处置表）已归档。
> 以下条目**维持现行口径不改**；如需重新评估，先读这里的理由，避免重复立项。

**维持不改 11 条**

| ID | 条目 | 维持理由 |
|---|---|---|
| R12-4 | 模块页 dirty 深比较 | 实为 computed（Vue 已缓存），报告「每次访问 stringify」证据不成立 |
| R13-6 | codegen `payloadSignature` 全量 stringify | watch 源形态带 600ms 防抖，字段表规模下开销可接受 |
| R19-2 | force-logout 的 pk 语义反直觉 | 语义可用，已有注释口径 |
| R22-2 | 登录日志 online 列对 HTTP 登录恒 "/" | 后端口径即如此，渲染已是合理降级 |
| R30-4 | Setting destroy API 面开放（UI 全遮蔽） | post_delete 热更闭环后删除已安全，保留管理 API 面 |
| R1.2-6 | 渠道 / 集成等默认关闭项 | 信息项非缺陷（运维知悉即可） |
| R3.2-2 | file / audit 模型层守护方法未下沉 service | 已随四域拆分归位域内，内聚合理 |
| R3.4-2 | 消息类文件组织散落残余 | 注册机制已统一（register_message），剩余为文件组织美观项 |
| R4.2-3 | 运行环境门槛（Python / Node 版本） | 技术栈门槛非缺陷，报告未列整改项 |
| R62-4 | 权限码命名漂移（`list:UserNotice` vs basename） | 改种子触碰存量角色授权，风险大于收益；门禁对账兜住 |
| R63-1 | 首页缓存键含用户维度 | 正确口径（数据权限过滤后聚合），注释自证 |

**证据过时（无需处置）6 条**：R08-3（field 导入导出不对称前提部分失实）、R19-3（空 pk 守卫早已在位）、
R22-1（时间过滤实为 range）、R54-4（metric 导出 skipped 口径前提从未成立）、R55-6（metric 双渲染条件已互斥）、
R57-5（linkage 剔除早有提示）。

**维持评估 4 条**（重审报告结论）

| 条目 | 维持口径 |
|---|---|
| screen command 无 owner 守卫 | 维持 shared 协作语义（command 已是管理员级权限点），docstring 明确 |
| message 设置页跨 settings / notifications 两域 | 维持聚合页（渠道 + 订阅 + 模板是「消息域」统一入口） |
| 同 app 双审批引擎并存 | 维持（`approval/README.md` 决策树为唯一裁决入口） |
| `system/utils/platform` 工具 vs 服务边界 | 命名分区已登记 + 代表模块已迁 services；全量重构登记滚动 |

**另行立项 1 条**：前端模块环完整破除（`api/base → utils/http → utils/auth → store → api/auth`）——
130 环已全豁免并接入 `check-module-cycles.mjs` 门禁；完整破除触碰全站导入图，待单独立项。

---

## 八、权威文档地图（长期有效）

| 主题 | 位置 |
|---|---|
| 长期任务 / 现状盘点 / 三阶层任务 / 债务 | 本目录 `长期任务方案-2026.10.md`（长期方向与优先级；原《长期开发计划》已清理） |
| 触发制 / 红线 / 时间窗 / 滚动债 | `docs/plans/触发制任务清单-长期.md` |
| 月度窗口 / 贯穿专项 / 候选池 | `docs/plans/下阶段长期计划-2026.10.md` |
| 决策记录（ADR 全集 + 索引） | `docs/adr/`（最新 ADR-092；`adr/README.md` 为索引） |
| 安全接受项 / 供应链台账 | `docs/asvs-mapping.md` |
| 发布 checklist / 运维 | `docs/ops/`（`release-checklist.md` / `deployment.md` / `upgrade-stock.md` / `log-archive.md` / `kernel-release.md`） |
| 指标与性能基线 | `docs/metrics.md`、`loadtest/baseline.json`、`xadmin-client/e2e/perf-baseline.json` |
| 架构 / 权限 / 契约 / 内核包 | `docs/architecture/`（`kernel-package.md` / `component-handbook.md` / `permission.md` / 框架开发遵循准则.md） |
| 二开入口 | `docs/guide/`（`first-module-30min.md` / `recipes.md` / `secondary-dev-quickref.md` / `menu-maintenance.md`） |
| 前端约定与门禁 | `xadmin-client/CONTRIBUTING.md` / `AGENTS.md` / `e2e/README.md` |

---

## 附：工作区文档整理记录

**2026-10-14（5.0 发版窗口）**：工作区根文档两轮整理——①已交付的一次性工作文档（vben 对标系列
任务书、对标参考截图等）移入工作区回收站 `_delete/workspace-working-docs-20261014/`（可恢复）；
②**工作区级台账入库**：本清单、原《长期开发计划》（已清理）、原 5.0.0 发布台账（已清理）由工作区根迁入；现行发版台账为 `RELEASE-5.0.1.md`
本目录 `docs/plans/`（纳入版本保存），`README.md`（工作区总览）与 `AGENTS.md`（AI 协作规范）
作为工具常驻约定文件留在工作区根；工具目录中的散落内容（`.codeartsdoer/temp/accept` 备份、
`.workbuddy/memory` 旧记忆、空 `.trae` / `.arts`）同批移入 `_delete/`（可恢复）。

**2026-10-09**：`NEXT-DEV-PLAN.md`（执行记录一~二十二）、`CODE-REVIEW-TASKS/`（13 份台账）、
两份代码审查报告、`PHASE-C-HANDOFF.md` 等历史台账的结论已收敛进本清单与仓库内两份台账；
原文归档件其后已清理，追溯文献编号（`T01-xx`~`T10-xx` / `O1-xx`~`O12-x` / `R60-x` / `TG-x`）
对应的上下文，以仓库内 plans 台账、ADR 与 git 提交记录为准。
