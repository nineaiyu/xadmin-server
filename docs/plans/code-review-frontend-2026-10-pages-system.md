# 2026-10 前端专项审查 · 分册：系统域（30 页）

> 主册：[code-review-frontend-2026-10.md](code-review-frontend-2026-10.md)。证据等级 A（直接读源码）/ B（注释与测试佐证）/ C（待运行时/视觉验证）；问题编号 fe-sys-*。横切结论引用编号：fe-F-01（RePlusPage 无内联 error/重试）、fe-C-01（声明式弹层）、fe-C-02（ElMessageBox 直用）、fe-D-01（String(error) 残留）。 Phase 1 已确认 30 页功能闭环通过，本册只看 UI/UX 层。
> 口径：路由为菜单驱动（路径以后端菜单种子为准），「路由」列记 defineOptions 组件名；四态记 loading/error/empty/denied，「框架」= RePlusPage 内建，「空白」= 无 list 权限时整页无输出（RePlusPage `src/index.vue:261` 的 `v-if="auth?.list"`）。

## 0. 页面清单

| 页面 | 组件名 | 组件文件 | 复用组件与 hooks | 元数据驱动 | 四态 L/E/e/D |
|------|--------|----------|------------------|------------|--------------|
| 用户管理 | SystemUser | user/index.vue | RePlusPage+ReSplitPane+UserTree；hook.tsx 装配 9 个子 hook | 是 | 框/fe-F-01/框架/空白 |
| 部门管理 | SystemDept | dept/index.vue | RePlusPage(isTree)；deptColumns/deptFormOptions/useDeptPanel | 是 | 框/fe-F-01/框架/空白 |
| 岗位管理 | SystemPost | post/index.vue | RePlusPage；usePostColumns/PostMembersDialog | 是 | 框/fe-F-01/框架/空白 |
| 标签中心 | SystemTag | tag/index.vue | RePlusPage；tagColumns/tagDeleteGuard | 是 | 框/fe-F-01/框架/空白 |
| 通讯录 | SystemDirectory | directory/index.vue | ReSplitPane+DirectoryAside/DirectoryMembers（usePagedList） | 否（专用 API） | 有/有(loadFailed)/有/有(ReEmpty+lock) |
| 角色管理 | SystemRole | role/index.vue | RePlusPage；useRoleMenuTree/RoleForm | 是 | 框/fe-F-01+menuAlert/框架/空白 |
| 数据权限 | SystemDataPermission | permission/index.vue | RePlusPage；RuleListEditor/TrialPanel/ScopeSelect | 是 | 框/fe-F-01+lookupAlert/框架/空白 |
| 模型标签字段 | SystemModelLabelField | field/index.vue | RePlusPage 只读台账 | 是 | 框/fe-F-01/框架/空白 |
| 菜单管理 | SystemMenu | menu/index.vue | 自绘 el-tree；MenuTreeToolbar/Row/ContextMenu+抽屉 | 否（全量树） | 有/无(仅 toast)/有(区分无匹配/无数据)/无显式 |
| 数据字典 | SystemDataDict | dict/index.vue | RePlusPage(isTree)；dictCellRenderers 等 6 模块 | 是 | 框/fe-F-01/框架/空白 |
| 凭据与密钥 | SystemCredential | credential/index.vue | ReReadonlyTable×3（CredentialTable） | 否（聚合页，有意） | 有/有(ReEmpty+retry)/有/有(ReEmpty+lock) |
| 我的数据范围 | SystemMyScope | my-scope/index.vue | ScopeStatsCard/DeptCardList/MemberDrawer；viewState 状态机 | 否（managed 端点） | 有/有(el-result+retry)/有/有(el-result) |
| 个人配置 | UserConfig | config/user/index.vue | RePlusPage；useConfigPage 共壳 | 是 | 框/fe-F-01/框架/空白 |
| 系统配置 | SystemConfig | config/system/index.vue | RePlusPage；useConfigPage+configKeyHints | 是 | 框/fe-F-01/框架/空白 |
| 公告管理 | SystemNotice | notice/index.vue | RePlusPage；MarkdownEditor/NoticeShowForm | 是 | 框/fe-F-01/框架/空白 |
| 公告已读 | SystemNoticeRead | notice/read/list.vue | RePlusPage；useNoticeReadColumns | 是 | 框/fe-F-01/框架/空白 |
| 文件管理 | SystemUploadFile | file/index.vue | RePlusPage+FileStatsBar/ReStat；统计抽屉 addDrawer | 是 | 框/fe-F-01/框架/空白 |
| 导出中心 | SystemExportRecord | export/index.vue | el-tabs 双面板；useRecordCenter 共壳 | 是 | 框/fe-F-01/框架/空白(页签内) |
| 主机监控 | SystemMonitor | monitor/index.vue | 自绘仪表盘 10 组件；useMonitor/WS/轮询 | 否（指标端点） | 有(分区)/有(dataUnavailable+retry)/有(trendEmpty)/按钮级 |
| 模块管理 | SystemModule | module/index.vue | ReReadonlyTable；useSystemModuleDraft/useModuleSave | 否（模块清单） | 有/有(el-result+retry)/N-A/按钮级 |
| 代码生成 | SystemCodeGen | codegen/index.vue | BaseConfig/FieldConfigTable/ArtifactPreview/AiDraftDialog/SavedPlans | 否（向导式） | 有(分区)/无(归一 null 静默)/有/有(modelsDenied alert) |
| 操作日志 | SystemOperationLog | logs/operation/index.vue | RePlusPage+cursor 分页 | 是 | 框/fe-F-01/框架/空白 |
| 登录日志 | SystemUserLoginLog | logs/login/index.vue | RePlusPage；loginLogColumns | 是 | 框/fe-F-01/框架/空白 |
| 脱敏规则 | SystemDataMaskRule | mask/index.vue | RePlusPage；MaskPreview | 是 | 框/fe-F-01/框架/空白 |
| 在线用户 | SystemUserOnline | online/index.vue | RePlusPage；onlinePoller 30s | 是 | 框/fe-F-01/框架/空白 |
| 账号风险 | SystemAccountRisk | account-risk/index.vue | RePlusPage+StatsPanel(ReStat)；useRisk* 6 模块 | 是 | 框+stats/有(stats alert+retry)/框架/有(ReStateContainer) |
| 任务执行 | SystemTaskExecution | celery/logs/index.vue | RePlusPage；taskExecutionColumns/WS 日志弹窗 | 是 | 框/fe-F-01/框架/空白 |
| 任务列表 | SystemTask | celery/task/index.vue | RePlusPage；useTaskRowActions/Toolbar | 是 | 框/fe-F-01/框架/空白 |
| Crontab 调度 | SystemTaskCrontab | celery/crontab/index.vue | SchedulePage 薄壳+CronExpressionField | 是 | 框/fe-F-01/框架/空白 |
| Interval 调度 | SystemTaskInterval | celery/interval/index.vue | SchedulePage 薄壳 | 是 | 框/fe-F-01/框架/空白 |

30/30 defineOptions 名齐备（grep 证 A）。四态量化：denied 显式 4/30（directory、credential、my-scope、account-risk），error 态与空态分离 8/30（上述 4 + monitor、module、account-risk、operation-log 例外见逐页），error 缺口引用 fe-F-01 共 19 页。

## 1. 逐页审查

### 1.1 菜单管理 SystemMenu（复杂页）
- **标识**：路由名 SystemMenu / `menu/index.vue`(192 行) + `components/`(12 件) + `utils/`(30+ 件)；自绘 el-tree + 抽屉编辑，非元数据驱动（树是主心智，注释明示旧布局取舍）。
- **UI 实现**：全宽树 + 右侧抽屉编辑；工具栏「定位（⌘K 搜索/类型/状态/层级/统计 tag）/操作（新增/权限码/审计/导入导出/回收站/更多）」分区；操作三层入口（工具栏、行内 hover+focus-within、右键）共用 `buildNodeActions` 一份清单不会漂移。行内：类型 tag、状态圆点、后代计数、路径等宽字体、危险项 danger 色（teleport 后非 scoped 块处理正确）。批量模式有独立 batchbar（勾选数 + 启停/删除按 batchUpdate/batchDestroy 权限显隐）。
- **一致性与体验**：令牌全走 EP var（对比度注释 AA 取 regular）；⌘K 聚焦是域内独有增强（与其他列表页习惯一致）；空态区分「无匹配/无数据」。ElMessageBox 直用见 fe-C-02 坐标（useMenuRename.ts:27 prompt、menuDrawerSwitch.ts:51 confirm）；useMenuMutations.ts:82 String(error) 同型 → fe-D-01 坐标。
- **问题清单**：
  - fe-sys-P2-02：树加载失败仅 toast（`useMenuRows.ts` getMenuData 业务失败 message error、catch 吞网络异常），树区落回 ReEmpty「暂无数据」——失败伪装成空树，无内联重试。同 fe-F-01 缺口但为自绘页需单独补。证据 A。
  - fe-sys-E-01：抽屉未保存切换有确认（menuDrawerSwitch unsaved），页面级无离开确认（见 E-01 汇总条目）。

### 1.2 我的数据范围 SystemMyScope（复杂页，域内四态基准页）
- **标识**：SystemMyScope / `my-scope/index.vue`(333 行) + MemberDrawer/DeptCardList/ScopeStatsCard；只读聚合页（`GET /identity/dept/managed`），非元数据驱动。
- **UI 实现**：`resolveMyScopeState` 状态机驱动 denied（el-result 说明权限点）/error（el-result + 重试）/empty/searchEmpty/ready 五态，统计卡仅 ready 渲染（失败不显示 0 误导）；卡片/层级双视图（Segmented），卡片网格响应式 4/2/1 列，层级用 el-tree-v2 虚拟树 + 全量展开键集合；渲染上限 RENDER_STEP=200 +「加载更多」（剩余计数可见），关键字变化重置上限。
- **一致性与体验**：管理员折叠 +N 带 title 全量；树内成员数 ml-auto 对齐；跳转用户管理均带 canListUsers 门控（禁用而非隐藏，与「precondition→disable」原则一致）；MemberDrawer 为声明式 el-drawer（fe-C-01 清单内）。
- **问题清单**：无新立问题。四态完备度域内最高（Phase 1 已证数据链）。

### 1.3 凭据与密钥 SystemCredential（复杂页）
- **标识**：SystemCredential / `credential/index.vue`(302 行) + CredentialTable；只读三源聚合（系统配置/Setting/模型字段），文件头注释明示「不迁 RePlusPage」的口径（无元数据面）。
- **UI 实现**：denied（ReEmpty+lock+hint）/error（ReEmpty+retry）/明文告警 alert（计数）→ 检索行 → 三张卡各含只读表 + 客户端分页（PAGE_SIZE=20，keyword 变化重置页码）；列集合即数据口径（注释），轮换/去更换动作列按 `row.rotatable && canRotate` / `change_entry` 权限收敛，无动作渲染「—」。
- **一致性与体验**：轮换为高危写操作：useConfirm 二次确认区分 model_field 与普通文案、行级 loading + 防并发（rotatingKey）；三表单元格收敛 CredentialTable 一处（status/masked/rotated/actions 同形）；description 空值「—」、掩码 font-mono。
- **问题清单**：无新立问题（error 无重试?——有 retry 按钮，符合）。

### 1.4 代码生成 SystemCodeGen（复杂页）
- **标识**：SystemCodeGen / `codegen/index.vue`(499 行) + 5 子组件 + payload/plan-service utils；向导式双栏（左配置 10 格/右产物 14 格），非元数据驱动。
- **UI 实现**：单/批量模式 radio；权限缺失给 modelsDenied/fieldsDenied alert（而非空下拉）；AI 草案按钮 disabled+tooltip 说明前置（选模型）；字段表 v-show=planData，空态 ReEmpty(planEmpty)；产物预览按落点分组（后端/前端/种子/文档）+ hljs 懒加载；实时预览 600ms 防抖 + autoPreview 开关；方案条（保存/载入/改名/共享/导出/删除/导入）在卡片头部。
- **一致性与体验**：模型切换先清场（防残留上一模型字段/产物）注释在案；SavedPlans 删除/共享用 useConfirm（危险确认基准）；AI 草案两段式人审（默认全选可取消）符合「AI 产物不直接生成」约定。AiDraftDialog 声明式 el-dialog（fe-C-01 清单内）；SavedPlans.vue ElMessageBox.prompt ×2 → fe-C-02 坐标；index.vue ×4 处 plan 失败消息插值 `error instanceof Error ? ... : error`（:250/:278/:290/:303）非 Error 对象时渲染 [object Object] → fe-D-01 坐标（http 层已 toast，属双报/乱码残留级）。
- **问题清单**：
  - fe-sys-P2-03：models/planFields/preview/download 四处失败 `.catch(() => null)` 归一静默（:128/:147/:177/:222，注释「http 层失败已统一提示」），界面落回空下拉/planEmpty/上次产物——无权限以外的失败（网络抖动、后端 5xx）与空态不可区分，也无重试入口。与 my-scope/credential「失败与空态分离」的域内基准不一致。证据 A。
  - fe-sys-E-03：方案工具栏 9 控件平铺卡片头部，<1280px 折行 3+ 行挤压配置区（C 待视觉验证）；可收敛「更多」下拉（SavedPlans 已是组件，改造成本低）。

### 1.5 主机监控 SystemMonitor（复杂页）
- **标识**：SystemMonitor / `monitor/index.vue`(362 行) + 10 子组件 + useMonitor/WS/轮询；仪表盘聚合页，非元数据驱动。
- **UI 实现**：HealthBanner（未知态显式「—」圈 + itemUnknown 回退，防 undefined 崩溃有测试）→ 不可用 alert（sectionErrors 粒度 + 重试）→ 工具条（WS 连接 tag、自动刷新开关、分享、导出下拉按 canExport 显隐）→ 资源卡 → 趋势卡（区间/粒度/指标筛选 + 摘要 chips + 导出图片）→ 状态面板/告警面板/事件流/任务健康/慢请求。趋势筛选与 URL query 双向同步（history.replaceState 防整页重建，注释在案）。
- **一致性与体验**：分享走 copyText 降级展示；导出报表 loading + message 归一（:155 String(error) 同型 → fe-D-01 坐标）；阈值按钮 canUpdateThreshold 门控。监控页四态在域内属完整档（分区 error + 重试）。
- **问题清单**：
  - fe-sys-E-02（部分）：趋势摘要条 `bg-gray-50 dark:bg-(--el-fill-color-light)`（:307）与 HealthBanner `text-red-500`（:125）用 Tailwind 原色非 EP 令牌，暗色下与 danger 令牌色相漂移（hex grep 无命中，此类为 Tailwind 类残留）。成本低。

### 1.6 模块管理 SystemModule（复杂页）
- **标识**：SystemModule / `module/index.vue`(337 行) + hook/useSystemModuleDraft/useModuleSave/moduleDraft；ReReadonlyTable + 草稿域，非元数据驱动。
- **UI 实现**：loadError → el-result + 重试（显式测试锚点）；概览卡（当前预设 tag/摘要/override 警示/重启提示 alert/基线行/已禁用清单）；pending 态卡展示期望预设 + diff tags（enable=success/disable=danger）+ 重启命令 tag + 复制按钮——把「改了什么、还要做什么」讲清楚，是域内信息层级最好的写操作页。编辑卡：预设 radio + 模块表（core 锁定 switch disabled + tooltip「coreLocked」）、保存前 diff 二次确认（saveConfirm 列出预设/启用/停用变更）、恢复基线确认。
- **一致性与体验**：canApply/canReset 控制按钮显隐（无权限回只读视图可接受，但无「只读因权限」说明，弱于 my-scope/credential 的 denied 说明）；脏检查只在预设切换时 discardConfirm。
- **问题清单**：无新立 P2；离开确认并入 fe-sys-E-01。

### 1.7 账号风险 SystemAccountRisk（复杂页）
- **标识**：SystemAccountRisk / `account-risk/index.vue`(88 行) + StatsPanel/RiskHandleForm + useRisk* 6 模块；RePlusPage 直驱 + 顶部统计条。
- **UI 实现**：无 list 权限 → ReStateContainer denied（域内标杆）；stats 失败 → alert + 重试（与列表分离）；巡检中 → alert 内嵌 el-progress（stage 文案 + 进度，1s 轮询 10min 上限，超时/失败/终态文案齐全，onUnmounted 清理）；统计 chips 点击映射列表筛选（useRiskQuickFilter）。
- **一致性与体验**：批量处置按钮随 selection-change 显隐（canBatchHandle computed，注释说明避免逐行重算）；处置表单动作清单以服务端 stats.actions 白名单过滤、缺省回退本地全集；单行/批量共用 RiskHandleForm 载荷。符合基准。
- **问题清单**：无。

### 1.8 文件管理 SystemUploadFile（复杂页）
- **标识**：SystemUploadFile / `file/index.vue`(75 行) + FileStatsBar/FileStatsPanel/FileUpload/PreviewDrawer + useSystemUploadFile；RePlusPage 直驱 + ReStat 指标条。
- **UI 实现**：常驻指标条（使用率进度按 80/95 阈值变色、无配额显示「不限」而非 0，tip 说明口径）与列表同宽（w-99/100 对齐注释）；重型图表收进 addDrawer 快照抽屉（防挤压列表首屏，注释实测 605px 依据）；列表 saved-views + advanced-filter + recycleBin 全开。
- **一致性与体验**：上传弹层（FileUpload）分片/断点续传 + 限流回退告警一次性标记 + 配置端点无权限时回退 10MB 并提示（默认值有出处）；上传去重优先展示服务端 detail。四态：列表侧同 fe-F-01；stats 无独立加载/失败态（stats 为 null 时整条隐藏——失败与未加载不可区分，量级小）。
- **问题清单**：无新立 P2（stats 失败静默隐藏记入观察，不立项）。

### 1.9 导出中心 SystemExportRecord（复杂页）
- **标识**：SystemExportRecord / `export/index.vue`(41 行) + Export/ImportPanel + useRecordCenter 共壳；双页签 RePlusPage，元数据驱动。
- **UI 实现**：页签写 URL（tabQuery，不触发路由重建）；useRecordCenter 收敛两页签：状态/动作字典 tag（字典色优先 + 本地兜底映射）、进度列（RUNNING 进度条/终态 100%/「—」，normalizeProgress 兼容 0-1 与 0-100 双口径有注释）、失败原因列截断+tooltip（排障不必开详情）、大小 formatBytes、5s 轮询仅在存在 RUNNING/PENDING 行时启动（watch dataList 启停 + onScopeDispose 清理）；重跑（RERUNNABLE 白名单 + rerun 权限）/下载（显式 catch 防双报）/日志（复用 ReTaskLogDialog）。
- **一致性与体验**：import 页签按 `hasAuth("list:SystemImportRecord")` 显隐，export 页签恒显（页签内 RePlusPage 无权限时空白）——不对称但实际不可达（菜单权限=列表权限绑定），记 E。
- **问题清单**：
  - fe-sys-E-04：export 页签无对称权限判断（import 有），低危一致性问题；顺手补 `hasAuth("list:SystemExportRecord")` 即可。

### 1.10 用户管理 SystemUser（中量页）
- **标识**：SystemUser / `user/index.vue`(105 行)；ReSplitPane 分栏（宽度持久化 useSplitPaneConfig，双击/悬浮重置）+ 部门树 + RePlusPage（saved-views/advanced-filter/recycleBin）；桌面分栏与移动堆叠共用一份 plusPageProps（防双份漂移）。
- 四态/权限态：L 框架+树 treeLoading；E fe-F-01；D 外层 `v-if="auth.list"` → 无权限整页空白（比 directory 的 lock 空态弱）。树搜索命中 `text-red-500`（UserTree.vue:201）纯色高亮 vs menu 树 menu-row__hit 令牌方案（danger 色+warning-light-9 底）——同域两套高亮口径 → 并入 fe-sys-E-02。hook 装配 9 子 hook 各有注释；行内重置密码/MFA/下线/邀请/模拟均有确认与 extraAuth 门控（userActions 测试在案）。
- **问题**：无新立 P2。

### 1.11 通讯录 SystemDirectory（中量页）
- **标识**：SystemDirectory / `directory/index.vue`(76 行)；ReSplitPane（与用户页同口径持久化）+ DirectoryAside/DirectoryMembers（usePagedList）。
- 四态/权限态：全四态显式（denied=ReEmpty+lock+hint；loadFailed 与空名录分离 + message 点名）——分栏只读页的四态基准。成员卡片响应式网格、列表/卡片双视图、空资料行收敛提示（hasProfile）；详情 addDrawer（命令式，符合基准）；移动端 compact 限高。
- **问题**：无。

### 1.12 部门管理 SystemDept（薄壳+）
- SystemDept / dept/index.vue(48 行)；RePlusPage isTree + 顶部硬删除警示 alert（「不可恢复」显式说明，危险语义前置）；「管理」抽屉入口按四个内容权限点显隐（无任何可看内容不渲染空抽屉，注释在案）；批量更新走 useActiveBatchUpdate 白名单。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.13 岗位管理 SystemPost（薄壳）
- SystemPost / post/index.vue(31 行)；RePlusPage recycleBin；成员分配/预览走 ReDialog 命令式；启停用列只读标签（防与内联开关双入口，注释）。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.14 标签中心 SystemTag（薄壳）
- SystemTag / tag/index.vue(36 行)；顶部用途提示 alert；authSuffix:"Tag"（历史命名显式映射）；受保护标签（内置/被引用）删除按钮隐藏 + 后端兜底（canDeleteGuard 有测试）。四态：框/fe-F-01/框架/空白。**问题**：无（保护行「隐藏而非禁用」与 codegen「禁用+tooltip」不同型，但注释给出了「必然失败→隐藏」的理由，不立项）。

### 1.15 角色管理 SystemRole（薄壳+）
- SystemRole / role/index.vue(46 行)；授权树不可用（缺 list:SystemMenu 或加载失败）时 menuAlert 顶置 + **关闭 create/update/partialUpdate**（注释：空树上保存会清空角色授权，比不能编辑危险）——域内「依赖不可用即禁写」的典范。四态：框/fe-F-01+menuAlert/框架/空白。**问题**：无。

### 1.16 数据权限 SystemDataPermission（薄壳+）
- SystemDataPermission / permission/index.vue(38 行)；lookupAlert 顶置（菜单/字段注册表不可用说明缺权限点或失败，并禁写）；列表 mode/范围 tag 语义化（单条规则标注「按或模式保存」防误读）；试算 TrialPanel + ScopeSelect（fe-C-01 清单内）。四态：框/fe-F-01+lookupAlert/框架/空白。**问题**：无。

### 1.17 模型标签字段 SystemModelLabelField（薄壳）
- SystemModelLabelField / field/index.vue(22 行)；只读台账：create/update/partialUpdate/importData/destroy/batchDestroy 全部代码级关闭（后端动作已下线，防 405/403 按钮尸体）；仅剩 sync 按钮。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.18 数据字典 SystemDataDict（薄壳+）
- SystemDataDict / dict/index.vue(35 行)；RePlusPage isTree；操作列 6 按钮全平铺（showNumber:6 + width:420 显式注释）；同层排序/批量启停/缓存刷新 extraAuth；色值/失败计数/锁定态 cellRenderer 收敛 dictCellRenderers。四态：框/fe-F-01/框架/空白。
- **问题**：fe-sys-E-05：操作列固定 420px 在 <1440px 视口挤压业务列（C 待视觉验证）；域内其他页 showNumber 3-4 收「更多」，本页全平铺为域内特例（有注释取舍，若视觉验证无碍可保留）。

### 1.19 个人配置 UserConfig（薄壳）
- UserConfig / config/user/index.vue(28 行)；useConfigPage 共壳：清除缓存行内按钮（confirm + loading + 刷新口径统一）。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.20 系统配置 SystemConfig（薄壳+）
- SystemConfig / config/system/index.vue(21 行)；注册键候选独立加载：失败 warning toast + failed 标记，表单每次打开重试（baseColumnsFormat 内注释）——局部依赖的失败重试口径正确；key 列自动补全 + 期望类型提示。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.21 公告管理 SystemNotice（薄壳+）
- SystemNotice / notice/index.vue(33 行)；行内编辑按钮按通知类型禁用（SYSTEM 类型走发布接口不可编辑，disabled+type=default 而非隐藏——precondition→disable 口径）；详情前 detailRowFetch 取原文（失败回退行快照）；URL notice_user 参数容错解析（非法 JSON 清理并中止，防 searchComplete 中断弹窗，注释+测试）。MarkdownEditor 394 行有独立测试与上传 hook。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.22 公告已读 SystemNoticeRead（薄壳）
- SystemNoticeRead / notice/read/list.vue(29 行)；框架默认删除/批量删除代码级关闭（与「移除」语义重叠且框架确认文案是「删除」，注释在案），保留带明确确认的「移除」（canRemoveNoticeReadRow 行级显隐）；全部已读确认 + handleOperation 口径。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.23 操作日志 SystemOperationLog（薄壳+）
- SystemOperationLog / logs/operation/index.vue(44 行)；审计百万行：withCursorPagination keyset 翻页（跳页/改排序回退 page 模式）；只读——destroy/batchDestroy 关闭（端点已下线防 405）；详情入口按 retrieve 权限显隐（仅 list 权限不露 403 入口）；导出面警示 alert（请求/响应正文截断上限，敏感数据预期管理）；慢请求标红阈值优先服务端端点回退 1s。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.24 登录日志 SystemUserLoginLog（薄壳+）
- SystemUserLoginLog / logs/login/index.vue(40 行)；顶部 logoutHint 说明纯 HTTP 会话需去在线会话页（作用域预期管理）；强退按钮 danger + 确认（仅 WS 在线会话可踢，其余行换「会话管理」跳转入口——按钮语义与能力对齐）；详情走 retrieve 全量。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.25 脱敏规则 SystemDataMaskRule（薄壳+）
- SystemDataMaskRule / mask/index.vue(42 行)；顶部停用风险警示（「停用立即还原明文」后果前置）；模型/字段候选无权限时降级手填（下拉→输入框，优雅降级）；labeled_choice/roles 兼容渲染 + 「—」兜底；预览弹窗（addDialog 命令式）有组件测试。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.26 在线用户 SystemUserOnline（薄壳+）
- SystemUserOnline / online/index.vue(24 行)；30s 轮询（页面隐藏跳过、keep-alive 失活即停，onMounted/onActivated/onDeactivated/onUnmounted 四口齐全）；`page.auth.batchDestroy = false` 关闭框架批量删除（确认文案是「删除」会误导，注释在案）；creator 列 el-link 跳用户详情。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.27 任务执行 SystemTaskExecution（薄壳+）
- SystemTaskExecution / celery/logs/index.vue(35 行)；导出/导入/报表任务与执行记录共用主键一个入口（注释明示不再有第二入口）；取消/重跑按独立权限点（聚合端点）显隐；进行中执行自动刷新（useExecutionAutoRefresh）；saved-views + advanced-filter。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.28 任务列表 SystemTask（薄壳+）
- SystemTask / celery/task/index.vue(29 行)；enabled 列覆盖框架布尔开关改走 enable 动作（启停只由 enable:SystemTask 授权 + django_celery_beat 保存信号，注释在案；失败也回拉数据防乐观态漂移）；行内立即执行/实时日志/克隆 + 工具栏批量执行/启停按 extraAuth。四态：框/fe-F-01/框架/空白。**问题**：无。

### 1.29 Crontab 调度 SystemTaskCrontab（薄壳）
- SystemTaskCrontab / celery/crontab/index.vue(27 行)；SchedulePage 共用薄壳（两菜单权限独立仅收敛装配）；day_of_week 可读化（列表/区间/步长/别名，无法解析回退原文，cronWeekday 有测试）；CronExpressionField 275 行可视化表达式 + 表达式引用弹层（scheduleReferences 用 ElMessageBox.alert → fe-C-02 坐标）；删除前引用检查确认（useScheduleDelete）。四态：框/fe-F-01/框架/空白。**问题**：无（除 fe-C-02 坐标引用）。

### 1.30 Interval 调度 SystemTaskInterval（薄壳）
- SystemTaskInterval / celery/interval/index.vue(20 行)；同 SchedulePage 薄壳；every 列渲染「下次近似时间」（dayjs 推算，period 元数据直出标签）；删除引用检查同上。四态：框/fe-F-01/框架/空白。**问题**：无。

## 2. 本分册小结

### 发现统计
- P0：0；P1：0。
- P2：3（fe-sys-P2-01 denied 空白不一致→移交主册框架项；fe-sys-P2-02 menu 失败伪装空树；fe-sys-P2-03 codegen 静默空区）。
- E：5（E-01 草稿离开确认；E-02 Tailwind 原色残留 4 处；E-03 codegen 方案工具栏折行；E-04 export 页签权限不对称；E-05 dict 操作列 420px）。
- 坐标移交（并入既有横切项，不新立）：fe-C-02 ← SavedPlans.vue:54/:69（prompt）、useMenuRename.ts:27（prompt）、menuDrawerSwitch.ts:51（confirm）、scheduleReferences.ts:26（alert）；fe-D-01 ← codegen/index.vue:250/:278/:290/:303、monitor/index.vue:155、useMenuMutations.ts:82、role/components/RoleForm.vue:133（已归一，仅列出处）。

### 移交全栈审查
1. fe-sys-P2-01（本册域内证据，修复在 RePlusPage 框架）：17 个 RePlusPage 直驱页无 list 权限时整页空白（`src/components/RePlusPage/src/index.vue:261` `v-if="auth?.list"`），而域内 4 页（account-risk ReStateContainer / my-scope el-result / credential、directory ReEmpty+lock）有显式 denied 态，user 页 `v-if="auth.list"` 同样空白。grep 口径：`src/views/system/**/index.vue` 无外层权限分支且无 denied 组件者 17 页；建议框架补 `ReStateContainer state="denied"`（影响全站约 37 页，需主册统一定夺）。
2. fe-F-01 域内确认：19 页 error 态依赖 toast/框架，无内联重试（逐页四态列已标）。

### Top 发现
1. fe-sys-P2-01 denied 空白不一致（框架级，域内 4 页已示范正确做法）。
2. fe-sys-P2-02/03 自绘页失败态伪装空态（menu 树、codegen 四区），与 my-scope/credential/monitor 的「error 与 empty 分离 + 重试」域内基准相悖。
3. 正面：role「依赖不可用即禁写防清空授权」、module「保存前可读 diff 确认」、operation-log「cursor 分页 + 权限收敛入口」为域内值得固化为模式的三个样板。

### 红旗 grep 汇总（口径：src/views/system，排除 *.spec 与 __tests__）
- `#[0-9a-fA-F]{3,8}`：3 命中——HistoryChart.vue:148、CategoryPieChart.vue:96（均为 cssVarColor 的 `#ffffff` 回退值，可接受模式）；MenuTreeToolbar.vue:416（注释）。真实硬编码色 0。
- `!important` 字面量：0 命中（Tailwind `!` 后缀修饰符为项目惯例，不计）。
- Tailwind 原色类（EP 令牌替代建议）：monitor/index.vue:307 bg-gray-50、HealthBanner.vue:125 text-red-500、UserTree.vue:57 dark:text-white!/:201 text-red-500 → fe-sys-E-02。
- `onBeforeRouteLeave|beforeunload`：0 命中（系统域无页面级离开确认）→ fe-sys-E-01。
- `ElMessageBox` 直用：5 坐标（见上）；useConfirm 命中 10+ 处为基准用法。

### 已读文件清单（A 级证据，均绝对路径省略前缀 /Users/isummer/sources/xadmin/xadmin-client/）
- 30 页入口：src/views/system/{user,dept,post,tag,directory,role,permission,field,menu,dict,credential,my-scope,config/user,config/system,notice,notice/read/list,file,export,monitor,module,codegen,logs/operation,logs/login,mask,online,account-risk,celery/logs,celery/task,celery/crontab,celery/interval}/index(.vue|vue)
- hooks/utils：user/utils/hook.tsx、dept/utils/hook.tsx、role/utils/hook.tsx、permission/utils/hook.tsx、dict/utils/hook.tsx、notice/utils/hook.tsx、notice/read/hook.tsx、mask/utils/hook.tsx、online/utils/hook.tsx、post/utils/hook.tsx、tag/utils/hook.tsx、field/utils/hook.tsx、config/useConfigPage.ts、config/system/utils/hook.tsx、logs/operation/utils/hook.tsx、logs/login/utils/hook.tsx、celery/{task,crontab,interval,logs}/utils/hook.ts(x)、export/utils/{hook.tsx,importHook.tsx,recordCenter.ts}、account-risk/utils/{hook.tsx,riskPageButtons.ts,useRiskScan.ts}、module/utils/{hook.ts,useModuleSave.ts,useSystemModuleDraft.ts}、menu/utils/{useMenuData.ts,useMenuRows.ts,useMenuInit.ts,useMenuMutations.ts(节选)}、codegen/utils/plan-service.ts(节选)
- 组件：menu/components/{MenuTreeToolbar,MenuTreeRow,MenuDrawerForm(节选)}.vue、my-scope/components/{MemberDrawer,DeptCardList,ScopeStatsCard}.vue、credential/components/CredentialTable.vue、codegen/components/{AiDraftDialog,SavedPlans,ArtifactPreview(节选)}.vue、monitor/components/{HealthBanner,MonitorToolbar}.vue、file/components/{FileStatsBar,FileUpload}.vue、directory/components/DirectoryMembers.vue(节选)、user/components/UserTree.vue(节选)、permission/components/ScopeSelect.vue(节选)、account-risk/components/{StatsPanel,RiskHandleForm}.vue、celery/components/SchedulePage.vue
- 框架口径：src/components/RePlusPage/src/index.vue（:261 denied 行为）
