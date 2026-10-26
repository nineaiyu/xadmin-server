# 2026-10 前端专项审查 · 分册：平台域（23 页）

> 主册：[code-review-frontend-2026-10.md](code-review-frontend-2026-10.md)。
> 覆盖：设置 8 + 分析 4 + 审批 6 + 集成 5。Phase 1 已确认功能闭环（见
> `code-review-2026-10-pages-{settings,approval,analysis-integration}.md`），本分册只审 **UI/UX 层**。
> 证据等级：A = 直接读到源码；B = 自调用链推断；C = 待验证（不定论）。问题编号 `fe-plat-*`。
> 横切结论引用不展开：fe-F-01（RePlusPage 无内联 error/重试）、fe-C-01（声明式弹层绕过 addDialog）、
> fe-C-02（ElMessageBox 直用）、fe-D-01（String(error) 残留）。

## 0. 页面清单

| # | 域 | 页面 | 入口组件（src/views/） | 形态 | 逐页结论 |
|---|----|------|------------------------|------|----------|
| 1 | 设置 | settings/basic | settings/basic/index.vue（99 行） | settings-shell 表单×3 | 通过 |
| 2 | 设置 | settings/security | settings/security/index.vue（151） | shell 表单×10 + RePlusPage | 通过（P2-01 在 open-platform，本页无独立问题） |
| 3 | 设置 | settings/sms | settings/sms/index.vue（106） | shell + 动态渠道页签 | 通过（失败态/重试最完整的设置页） |
| 4 | 设置 | settings/message | settings/message/index.vue（126） | shell 表单×5 + 2 自定义面板 | 通过 |
| 5 | 设置 | settings/ledger | settings/ledger/index.vue（24） | RePlusPage 只读 | 通过（fe-F-01 标注） |
| 6 | 设置 | settings/ldap | settings/ldap/index.vue（34） | shell 单面板 | 通过 |
| 7 | 设置 | settings/open-platform | settings/open-platform/index.vue（67）+ 3 面板 | shell + 卡片表单/只读表 | **P2-01 / E-01** |
| 8 | 设置 | settings/login-policy | settings/login-policy/index.vue（32） | RePlusPage | 通过 |
| 9 | 分析 | dashboard 画布 | dashboard/index.vue（351）+ 9 composables | 自绘卡片栅格 | **P2-02 / E-03** |
| 10 | 分析 | dashboard/dataset | dashboard/dataset/index.vue（203） | RePlusPage + 预览弹窗 | 通过（fe-C-01 引用） |
| 11 | 分析 | analysis/report | analysis/report/index.vue（31）+ designer.vue（400） | RePlusPage + 设计器 | 通过 |
| 12 | 分析 | analysis/screen | analysis/screen/index.vue（31）+ designer.vue（437）+ display.vue（431） | RePlusPage + 双设计器 + 投屏 | 通过（fe-C-01/fe-D-01 引用） |
| 13 | 审批 | approval/index | approval/index.vue（44） | 双页签 + 统计 + 角标 | 通过 |
| 14 | 审批 | approval/flow | approval/flow/index.vue（23）+ 配置/版本抽屉 | RePlusPage + vue-flow 画布 | 通过（fe-C-02 引用） |
| 15 | 审批 | approval/instance | approval/instance/index.vue（140） | 五页签 + 批量 + 详情抽屉 | 通过（批量反馈标杆） |
| 16 | 审批 | approval/leave | approval/leave/index.vue（52） | 统计 + RePlusPage | 通过 |
| 17 | 审批 | approval/delegation | approval/delegation/index.vue（19） | RePlusPage + 影响面守卫 | 通过 |
| 18 | 审批 | approval/rule | approval/rule/index.vue（29） | RePlusPage + 影响面预览 | 通过（fe-C-02 引用） |
| 19 | 集成 | ai/index | integration/ai/index.vue（334） | 左右分栏会话台 | 通过（E-02） |
| 20 | 集成 | ai/config | integration/ai/config.vue（114） | settings-shell 三面板 | 通过 |
| 21 | 集成 | knowledge | integration/knowledge/index.vue（34）+ 上传/构建 | RePlusPage + 弹窗 | 通过 |
| 22 | 集成 | ai/mcp | integration/ai/mcp/index.vue（29） | RePlusPage + 工具/日志抽屉 | 通过 |
| 23 | 集成 | api-app | integration/api-app/index.vue（137） | RePlusPage + 密钥弹窗 + 用量抽屉 | 通过（fe-C-01 引用） |

## 1. 逐页审查

### 1.1 settings/basic（基本设置）

**标识**：`/settings/basic` → `settings/basic/index.vue`；复用 settings-shell（index.vue 338 行 + SettingItem.vue 318 行）与 `settingAuth`；非元数据驱动（表单列由后端 search-columns 下发、前端 fields 白名单过滤）。

**UI 实现**：三页签（基本 / 水印 / 资源告警）统一走 shell：左导航 + 内容纸、tablist 语义 + 方向键导航、`?tab=` 同步、懒挂载不销毁。水印页签为自定义面板（WatermarkSetting.vue）：预览区复用 App 同一水印渲染链路，表单 change 经 SettingItem change 事件防抖 200ms 重绘，previewNote 明确区分「已停用 / 当前路径不在范围」两种不生效原因（`data-testid="watermark-preview-note"`）——「开关开了但看不到」的可观测性补丁到位。SettingItem 四态：loading（v-loading）、列元数据失败 → `loadFailed` + 重试按钮、retrieve 无权限 → ReEmpty no-permission、空表单默认值兜底；「实际生效状态」effective 诊断横幅随读取与保存双路刷新（SettingItem.vue:65-78/186-190）。

**一致性与体验**：符合基准（message/ReEmpty/固定 LABEL_WIDTH 260px 对齐控件列/窄屏 label-position=top 回退）。保存即时刷新会话水印（onSaved → refreshSiteWatermark）是设置域即时反馈标杆。

**问题清单**：无。

### 1.2 settings/security（安全设置）

**标识**：`/settings/security` → `settings/security/index.vue`；10 个表型页签（verifyCode/captcha/login/limit/mfa/register/resetPassword/passwordRule/bindEmail/bindPhone）+ blockIp 列表页签；复用 settings-shell。

**UI 实现**：页签数组纯声明式（28-119 行），每个面板独立权限位（`settingAuth` 三位）。blockIp 面板仅在 `list:SecurityBlockIp` 时并入导航（129-134 行），隐藏而非禁用，与 shell 的 paneList 过滤口径一致；面板内 RePlusPage 带 `adaptiveConfig.offsetBottom: 160` 适配 shell 内嵌高度。四态：表型页签由 SettingItem 承担（同 1.1）；blockIp 列表走 fe-F-01。

**一致性与体验**：页面本体 151 行全部是配置装配，无手写交互，符合「设置页 = 面板清单」的域内范式。资源告警面板从本页迁出后 `localeName: "settingSecurity"` 保留（文案命名空间未随页面迁移漂移）。

**问题清单**：无。

### 1.3 settings/sms（短信设置）

**标识**：`/settings/sms` → `settings/sms/index.vue`；静态 server 面板 + 动态渠道 backends 子页签（`settingsSmsServerApi.backends()` 驱动）。

**UI 实现**：动态页签三态齐备——拉取期 `v-loading` 挂 shell 容器（39 行注释明确「缺失子页签属可见过渡态」）、业务失败 → 页签外 `el-alert` warning + 重试按钮（86-96 行）、网络异常同入口（74-77 行）；重试前清空旧渠道避免「旧页签 + 失败横幅」并存（48 行）。渠道子页签 label 优先 i18n（`te()` 探测）回退后端 label（57-59 行）。

**一致性与体验**：设置域里唯一带「列表级失败态 + 重试」的页，是 shell 页面失败态的参照实现（open-platform/message 面板同构）。符合基准。

**问题清单**：无。

### 1.4 settings/message（消息设置）

**标识**：`/settings/message` → `settings/message/index.vue`；email + 3 个 IM 渠道（钉钉/企微/飞书，fields 白名单 + `?channel=` 作用域）+ webhook + 通知订阅（ReMessageNotifications）+ 消息模板（MessageTemplatePanel.vue 333 行）。

**UI 实现**：渠道拆分把「测试按钮定位」收敛到渠道参数（22-25 行注释），避免「一个测试按钮测三家」歧义；订阅/模板页签无对应权限不进导航（102-107 行）。MessageTemplatePanel：注册表规模小走客户端过滤（关键词 + 分类），覆盖状态列用「已自定义 / 代码默认 + 停用」双标记表达心智模型，面板顶部 info alert 说明「代码默认 → DB 覆盖层，可重置」；加载失败 → error alert + 重试（248-260 行），不把权限回收误读成「没有模板」；重置走 useConfirm（186-196 行），编辑走 addDialog + useDialogForm + 保存前预览（TemplatePreview 只读弹窗），动作按 save/preview/reset 独立权限点显隐（38-40 行，注释「只读角色点开必 403」）。

**一致性与体验**：符合基准（addDialog/useConfirm/normalizeError/ReReadonlyTable）。工具行两个输入框用内联 `style="width: 240px/180px"`（227-245 行）——项目其余处用 `w-*!` 原子类，观感一致，不算问题。

**问题清单**：无。

### 1.5 settings/ledger（设置台账）

薄壳页：`settings/ledger/index.vue` 24 行，`defineListPage` 装配后显式关停 create/update/destroy/import 六个写权限位（hook.tsx:12-17），只读台账语义由前端按钮隐藏 + 后端视图双重收口。单根包裹注释解释了 Transition 白屏陷阱。四态：fe-F-01 适用（列表页无内联 error/重试）；空态由 RePlusPage 内建。符合基准。无问题。

### 1.6 settings/ldap（LDAP 设置）

薄壳页：34 行，shell 单面板（`multiPane=false` 不渲染左导航，内容纸铺满，避免孤立导航项——shell index.vue:17 注释），`desc` 给「测试只验连接」的边界说明。四态由 SettingItem 承担（含 test 按钮位）。符合基准。无问题。

### 1.7 settings/open-platform（开放平台）

**标识**：`/settings/open-platform` → `settings/open-platform/index.vue`（67 行）+ ScimPanel.vue（173）/OAuthProviderPanel.vue（166）+ OAuthProviderCard.vue（235）/AuthorizedAppAdminPanel.vue（60，复用 `@/components/AuthorizedAppTable`）。三页签按 retrieve 权限拼装（隐藏式，21-45 行）。

**UI 实现**：
- ScimPanel：SettingItem 表单 + 令牌轮换（warning 色 + rotateHint 文案）+ 同步记录 ReReadonlyTable（size=small 密度注释明确）。轮换成功用 `ElMessageBox.alert` 展示一次性 token（fe-C-02 引用），含「仅显示一次」warning 与复制按钮（fe-C-02 引用；fe-D-01 同族 String(error) 见 E-01）。
- OAuthProviderPanel：卡片式多 provider 表单，卡片头含启停开关/类型 tag/测试/删除；高级配置收进 el-collapse（一级字段 8 项、高级 12 项，信息分层合理）；client_secret 用 show-password + `secretConfigured` 占位提示已配置态；原始 JSON 模式弹窗（fe-C-01 引用 :139）带格式校验错误内联展示（49-57 行）。卡片删除有 danger 确认（OAuthProviderCard.vue:66-78，仅本地未保存也会确认，偏保守但无害）。
- AuthorizedAppAdminPanel：管理端撤销语义（撤全部用户授权）换 confirmKey 文案，复用个人端表格组件，权限门 `canRevoke` 透传。

**一致性与体验**：fe-C-02 两处（ScimPanel.vue:54、OAuthProviderPanel.vue:75）均为「结果展示」语义的 alert，非确认语义，危害低。四态：OAuth 面板有 loadFailed + 重试（96-100 行）与空态（123-126 行），SCIM 同步记录失败静默（112 行注释「辅助信息不阻断」）。

**问题清单**：
- **fe-plat-P2-01（P2·危险操作缺确认）**：SCIM 令牌轮换一键直达无二次确认——`ScimPanel.vue:85-100` `rotateToken` 直接调 `settingsScimApi.rotateToken()`，旧 token 立即失效、所有在用 SCIM 客户端同步断连，属高危不可逆操作；当前仅靠旁侧静态 hint 文案。同页参照系齐备：OAuth 卡片删除有 danger 确认（OAuthProviderCard.vue:71-77）、审批流回滚有 danger 确认 + 备注输入（FlowVersions.vue:71-83）、api-app 密钥重置有 confirmRegenerate。价值：避免误触即断生产集成；成本：一个 `useConfirm` 调用（≈5 行）；依赖：无。证据 A。
- **fe-plat-E-01（E·String(error) 新实例）**：`ScimPanel.vue:96` `message(String(error))` 与 `useOAuthProviders.ts:58/78` `detail: String(error)` 三处把异常对象字符串化直出（用户可见 "TypeError: ..."），与已立项 fe-D-01（ScreenControlForm.vue）同族；建议统一 `normalizeError` 收口。价值：错误文案可读；成本：低；依赖：无。证据 A。

### 1.8 settings/login-policy（登录访问策略）

薄壳页：32 行，RePlusPage + `addOrEditOptions`（时段/网段表单）+ `tableBarButtonsProps`（预演）。亮点：无 `auth.list` 时整页给 `ReEmpty` no-permission（27-31 行，`data-testid` 齐备）而不是空白页——薄壳页权限态的正面样本。四态：fe-F-01 适用。无问题。

### 1.9 dashboard/index（仪表盘画布）

**标识**：`/dashboard/index`（kiosk 展板路由复用同组件，`meta.kiosk` 隐藏全部编辑控件）→ `dashboard/index.vue`（351 行）+ DashboardToolbar/CardHeaderActions/ChartCardAsync/CardDrillDrawer + 12 个 composables（view/editing/delete/dialogs/linkage/drill/snapshot/autoRefresh/channel…）。非元数据驱动。

**UI 实现**：
- 布局：工具栏卡（选择器 + 编辑态动作 + 浏览态动作）→ 联动 chip 条 → 12 列响应式栅格卡片（xs 全宽/sm、md 半宽/lg 用户档位，253-259 行注释解释 EP 断点 min-width 语义陷阱）→ 编辑态追加「添加卡片」。
- 信息层级：卡片头 = 拖拽手柄（编辑态 CSS 点阵）+ 标题 + 数据集 tag + 动作组；浏览态给导出图片/下钻，编辑态给上移/下移/设置/移除（CardHeaderActions 按 editing 分支渲染，注释说明权限位在页面级判定）。
- 联动反馈：点击维度值 → `dashboard-linkage-chip`（el-tag closable + title 提示）一键清除回全量（228-248 行）；联动/切盘/进编辑均复位联动（170-176 行）。
- 草稿语义：编辑期变更落 draftLayout，保存才 PATCH（useDashboardEditing）；切换仪表盘经 setSwitchGuard 强制退编辑。
- 四态：loading（v-loading）、无仪表盘 ReEmpty、卡片数据失败由 ChartCard 内部态承担（B）、权限位 canEdit/canCreate/canDestroy 分别门控工具栏动作（隐藏式）。下钻抽屉自带 failed + retry（CardDrillDrawer.vue:123-127）。

**一致性与体验**：fe-C-01（CardDrillDrawer.vue:95 el-drawer 直用）引用。删除仪表盘有 danger 确认（useDashboardDelete.ts:24-31）；卡片移除仅动草稿、可取消恢复，不确认合理。图片导出/快照导出均有按钮级 loading。

**问题清单**：
- **fe-plat-P2-02（P2·草稿静默丢失）**：编辑态「取消编辑」与「切换仪表盘」均无 dirty 确认——`DashboardToolbar.vue:79-85` 的取消按钮直发 `toggleEdit`，`useDashboardEditing.ts:44-55` else 分支直接 `stopEditing()` 丢弃整份草稿；`index.vue:78` `setSwitchGuard(stopEditing)` 同样静默。用户重排/增删多张卡片后一次误触即全部丢失。对照组：报表/大屏设计器同场景有 `useDesignerGuard`（dirty 路由守卫）+ 头部「未保存」标签；本页有草稿模型却没有 dirty 判定。价值：与设计器页守卫口径拉平，消除最常见的静默丢数据点；成本：低（draftLayout 与 current.layout 做一次深比较，取消/切盘前 useConfirm）；依赖：无。证据 A。
- **fe-plat-E-03（E·工具栏信息层级）**：`DashboardToolbar.vue` 单排 flex-wrap 平铺约 11 个控件（选择器/新建/编辑/保存/删除/设置/快照/分享/刷新/自动刷新/数据集入口），编辑态与浏览态动作混排、换行后「保存」主操作与低频动作视觉同级；`flex-1` 左右分组是唯一的层次手段。价值：降低编辑态误触率（危险色删除紧邻主保存按钮）；成本：中（分组分隔线 + 编辑态收纳次级动作进「更多」）；依赖：无。证据 A。

### 1.10 dashboard/dataset（数据集）

**标识**：`/analysis/dataset/index`（URL 与目录不同名，index.vue:133-139 注释明确护栏口径）→ `dashboard/dataset/index.vue`（203 行）+ useDataset/datasetPreview。

**UI 实现**：RePlusPage 列表 + 执行预览弹窗（fe-C-01 引用 :52，既定保留的手写场景）：明细/聚合双视图 radio 切换、明细视图给总数 + DataTable（max-height 380）+ CSV 导出；聚合视图参数条（分组/指标/数值/粒度）+ 运行按钮 loading + 结果表 v-loading + 空结果 ReEmpty（194-198 行）。取数失败归一为可读 message、不打开空弹窗（datasetPreview.ts:104-119）；行/时间列统一 formatPreviewCell 防止 `[object Object]`（96-100 行）。聚合数值字段候选 = numeric_columns ∩ 可见列（62-66 行），字段权限裁掉的列不可选。

**一致性与体验**：四态：列表走 fe-F-01；弹窗内 loading/empty/失败提示齐备。导出 CSV 带 BOM（csv.ts），与预览同函数保证口径一致。

**问题清单**：无。

### 1.11 analysis/report（报表）

**标识**：列表 `/analysis/report/index` → index.vue（31 行薄壳，RePlusPage + operationButtons）+ designer.vue（400 行设计器，由列表行按钮进入）。

**UI 实现**（设计器）：三栏（左侧模板预设/明细列勾选/行数上限/组件库，中部明细表实时预览 + 组件网格，右侧 280px 属性面板）。状态可见性：头部「未保存」warning tag + 只读 info tag（`data-testid="designer-readonly"`）双标记；保存/运行按钮按 `readonly || datasetMissing` 禁用且给 `title` 原因（datasetUnavailable 优先于 readonlyHint，73-79 行）；数据集不可见/被删 → 画布区显式 ReEmpty 而不是空数据取数（244-249 行）；未保存守卫 `useDesignerGuard` + Ctrl/Cmd+S（useSaveShortcut，与设计器共用）。立即运行按**已保存**设计投递、有脏改动先提示保存（runNow 语义，注释 43 行）。

**一致性与体验**：四态完整（loading/empty×3/readonly/disabled-with-reason），是平台域「禁用态必须给原因」的正面基准。列表页 fe-F-01 适用。

**问题清单**：无。

### 1.12 analysis/screen（大屏设计器 + 投屏）

**标识**：列表 `analysis/screen/index.vue`（31 行薄壳）+ designer.vue（437 行，编排层）+ display.vue（431 行投屏）+ components/×16 + utils/×40（含 19 个测试文件）。非元数据驱动。

**UI 实现**：
- 设计器：顶部工具条（标题/未保存与只读 tag/栅格↔自由模式切换/主题选择器/撤销重做/复制/刷新/预览/清空/保存，DesignerToolbar.vue）+ 自由画布第二行工具条（缩放/复制粘贴/吸附/模板，仅自由模式且未锁定出现）+ 左组件面板 + 右属性面板（单选 PaneInspector / 多选 MultiSelectInspector 分派）。**undo/redo 可见**：canUndo/canRedo 驱动按钮禁用态（138-141 行 + Toolbar 96-111 行），两模式各持独立历史栈（136 行注释），快捷键全量（useScreenShortcuts）。清空画布有确认且仅清当前模式、另一模式载荷保留（useScreenPersistence.ts:65-83，pushHistory 后清空可撤销）。只读 = 无权限或非创建者，编辑入口整体隐藏/禁用 + readonly tag。
- 投屏 display.vue：三形态同页（自由画布 > 栅格画布 > 仪表盘轮播，与服务端同序）；header 含返回/标题连读/页码/远程接管 warning tag（manual 态）/时钟/主题面板（本机临时切换 + 保存为默认）/ZIP 导出/快照/暂停/全屏；loadFailed → ReEmpty + 重试（258-266 行）、空轮播 ReEmpty、全屏失败静默（111-121 行，注释说明浏览器策略拒绝是常态）。可见性口径：浏览者不可见的仪表盘/数据集整格跳过（56-58 行）。
- 主题：画布恒深色保证所见即所得（designer style 注释），色值一律走 `--screen-*` 令牌。

**一致性与体验**：fe-C-01（TemplateVersionsDialog.vue:86 el-dialog 直用）与 fe-D-01（ScreenControlForm.vue String(error)）引用；模板回滚有 danger 确认（TemplateVersionsDialog.vue:52-66）。远程控制指令失败保持弹窗可重试（ScreenControlForm apply 注释）。

**问题清单**：无新增（横切项已立项）。

### 1.13 approval/index（审批中心 · 轻量）

**标识**：`approval/index.vue`（44 行）+ ApprovalResourceTabs（路由驱动的资源页签）+ ApprovalStats（共享统计卡）+ ApprovalPanel（scope 驱动双页签）。

**UI 实现**：待我审批页签带角标（`useApprovalBadge` 单例：顶栏铃铛与本页签同源，60s 轮询 + 服务端 10s 缓存，`max=99` + 0 时隐藏）；mine 页签 lazy。统计卡无 `stats:` 权限整块不渲染不发请求（ApprovalStats.vue:62/120），失败显示「—」占位而非 0（66 行注释「不能把 0 当成真实指标」），并注册事件总线在动作后自动重拉。批量操作仅在 pending 页签装配（ApprovalPanel.vue:33-35）。

**一致性与体验**：行内通过走 `ElMessageBox.prompt` 采集意见（fe-C-02 引用 approvalRowButtons.ts:44），注释明确「输入弹窗承载确认语义、取消即中止」——确认语义成立，但与流程实例页的 ApproveForm 弹窗（意见 + 后果说明更完整）存在同域两种通过交互；属历史分册已记录的轻量/流程双轨差异，不另立项。撤回有 confirm.title（approvalRowButtons.ts:98-103）。fe-F-01 适用（列表）。

**问题清单**：无。

### 1.14 approval/flow（流程定义）

**标识**：`approval/flow/index.vue`（23 行薄壳）+ utils/hook.tsx + useFlowDrawers.tsx（配置抽屉/版本抽屉）+ FlowConfigDrawer/FlowFieldsEditor/FlowNodesEditor/FlowCanvas（@vue-flow 只读可视化）。

**UI 实现**：配置抽屉 lg + `closeOnClickModal: false` + **dirty 关闭守卫**（useFlowDrawers.tsx:61-68，`isDirty` 时 useConfirm）；编辑前先 retrieve 全量定义，取不到直接中止并提示（37-49 行注释「空节点编辑器保存会覆盖线上定义」——把「打开即丢数据」的失败模式堵在入口）。版本历史抽屉：当前版本 success tag、其余行回滚动作（danger + auth 门控），回滚用 ElMessageBox.prompt 采集备注且 confirmButtonClass 着 danger 色（fe-C-02 引用 FlowVersions.vue:72，注释解释 prompt 焦点在输入框无回车误确认）；回滚语义在抽屉顶部 info alert 说明「只影响之后发起的申请」。画布仅可视化 + 拖布局（分支增删改收口在列表编辑器，FlowCanvas.vue 注释），条件边带 animated + 摘要 label。

**一致性与体验**：四态：版本列表加载失败 → warning message（61-68 行），抽屉内列表空态由 ReReadonlyTable 内建；配置抽屉加载失败有显式 message。fe-F-01 适用（列表）。

**问题清单**：无。

### 1.15 approval/instance（流程审批 · 五页签）

**标识**：`approval/instance/index.vue`（140 行）+ InstancePanel×5（pending/mine/done/cc/ongoing）+ utils/×15（rowButtons/batchActions/dialogs/nodeActions…）+ components/×12（ApproveForm/RejectForm/InstanceDetail/InstanceDiscussion…）。

**UI 实现**：
- 页签组织：pending 常驻、其余 lazy；重访页签激活即重拉（41-49 行，注释说明在途单会在别处流转）；ongoing 页签按 `ongoing:` 权限点显隐（52 行）；pending 角标与顶栏铃铛共享计数（58 行注释）。
- 行内动作：待办六动作按 my_task 收敛显隐（instanceRowButtons.ts:92-188，「列表数据可能已被他人处理」的服务端口径前置到按钮）；撤回/重提带 confirm.title（插值单标题）；催办按 申请人本人 + PENDING 显隐（服务端 10 分钟节流）。index 顺序权重注释明确「避免行级条件按钮并列最前」。
- **批量反馈（本轮审查标杆）**：批量通过/驳回/转交标题插值勾选条数（instanceBatchOutcome.ts:85 `{n: pks.length}`）；无选中先 message 中止不弹窗（useInstanceBatchActions.ts:27-34）；服务端逐条独立校验，部分失败 → warning 携带「成功数 + 失败数 + 明细（转交限 3 条）」（batchSuccessHandler 34-55 行）；批量转交全失败弹窗保持打开便于改人重试（57-63 行 + handleFailed）。
- 详情抽屉：`openInstanceDetail` 用 addDrawer（md，移动端 full——instanceDialogs.tsx:61-63 注释给 175px 不可读的量化理由）；InstanceDetail 内容四态齐备：v-loading / 表单数据与轨迹空态 ReEmpty / **loadError → ReStateContainer error + 重试按钮**（325-345 行）/ 轨迹时间线（加签 tag、代理来源 tag、意见缩略）。标题含单号前 8 位大写。
- 发起申请：StartInstanceDialog 走 addDialog（hideFooter 自管提交）；成功回调统一刷新角标 + 统计 + 相关页签，且避免双请求（60-72 行注释）。

**一致性与体验**：弹层全部走 addDialog/addDrawer（本页无 fe-C-01 实例）；确认文案均插值业务标识（单号/标题）。fe-F-01 适用（列表）。

**问题清单**：无。

### 1.16 approval/leave（请假申请）

**标识**：`approval/leave/index.vue`（52 行）+ utils/×9（columns/formColumns/rules/save/runner…）。

**UI 实现**：顶部 ApprovalStats 复用（loader/authCode/labelKeys/tipKey 四路定制，统计卡双轨复用的第二个消费方，29-41 行），提交/撤回后随列表一起刷新（statsRef 透传）。列表与表单走 RePlusPage（`addOrEditOptions` 动态选项）。

**一致性与体验**：四态：统计卡失败「—」占位（同 1.13）；列表 fe-F-01 适用。提交/撤回刷新联动由 hook 承担（leaveSave.ts 有单测覆盖）。

**问题清单**：无。

### 1.17 approval/delegation（审批委托）

薄壳页：19 行 + utils/×6。亮点：启停开关带**影响面守卫**——停用前查 `impact` 接口，有在途受影响待办时 useConfirm 弹「委托人 x 件 / 代理人 y 件」量化后果，取消则抛错让开关回滚（delegationToggle.ts:23-47，注释明确走 renderSwitch exception 分支）。危险操作确认 + 后果说明 + 状态回滚三件套，平台域开关类交互的正面基准。fe-F-01 适用。无问题。

### 1.18 approval/rule（审批规则）

**标识**：`approval/rule/index.vue`（29 行）+ utils/×5 + components/×4（RuleLevelsEditor/RulePathsEditor/ApprovalRuleImpactField）。

**UI 实现**：RePlusPage + 表单弹窗内嵌「影响面预览」字段：按草稿实时值（路径/方法/优先级/启停）查在途命中单据，alert 结果含总数、未启用警示、逐条「草稿胜出 / 被现有规则压制」着色（ApprovalRuleImpactField.vue:36-84），截断给 `impactTruncated` 提示；无权限时按钮禁用 + 点击给可读提示（88-91 行，双保险）。错误内联展示于按钮旁（139-141 行）。

**一致性与体验**：fe-C-02 引用（:82 ElMessageBox.alert 承载结果，信息型非确认型）。规则列表 fe-F-01 适用。「改规则前先看影响面」的交互设计是平台域配置类表单的加分项。

**问题清单**：无。

### 1.19 integration/ai/index（AI 助手控制台）

**标识**：`integration/ai/index.vue`（334 行）+ AiFeatureNav/AiChatPanel（壳收敛到 MessageThreadPanel，与聊天室同套）/AiMessageRow/ToolCatalogDrawer + hooks/×8 + utils/×11。左右分栏会话台（与聊天室同布局口径）。

**UI 实现**：
- 页面级四态完备：状态接口失败且无任何可用权限 → ReResult error + 重试（221-231 行）；未启用 → ReResult warning；未配置 → ReResult info + 「去配置」入口；状态失败但问答权限可用 → 降级进入 + 顶部 warning alert（249-256 行，`resolveAiConsoleReady` 不再把整页判死）。
- 入口按权限点组装（ask/interpret/action 三位），权限变化当前入口失效回落首个（142-146 行）；窄屏左栏折叠为 ReNavDrawer。
- `?q=` 预填：切问答入口 + 填输入框 + 消费后从 URL 移除防刷新重复（189-197 行）；keep-alive 二次进入重拉状态且避免双请求（199-214 行）。
- 会话面板：Enter 发送/Shift+Enter 换行、流式气泡 + 停止、失败重试（重发最近提问）、向上加载更多、新消息悬浮条；「仅最新一条 assistant 消息可操作」的卡片时效口径（94-103 行注释）与历史回看一致。
- 工具目录：命令式 addDrawer 只读清单，入口按钮带数量。

**一致性与体验**：弹层走 addDrawer；高度用 `useFullHeightPanel` 视口实测替代 calc 魔数（174-176 行注释）。控制台边框/圆角走令牌（326-334 行）。

**问题清单**：
- **fe-plat-E-02（E·失败态缺口）**：工具清单加载失败只有一次性 warning toast（index.vue:91-95 `message(t("ai.toolsLoadFailed"))`），此后「工具目录」入口因 `tools?.tools.length` 为空不渲染，用户无任何重试入口，只能退出页面靠 keep-alive onActivated 重拉；指令执行入口的 subtitle 亦无法显示工具数。价值：补齐可观测性（此前该失败是静默的，本次已加 toast，属半程修复）；成本：低（失败态下在工具目录触发位渲染一个带重试的占位或复用降级 alert 槽位）；依赖：无。证据 A。

### 1.20 integration/ai/config（AI 配置中心）

**标识**：`integration/ai/config.vue`（114 行）+ AiActiveProfilesBar/AiGlobalSettingsCard/AiObservabilityPanel + useAiProfiles。复用 settings-shell（与系统设置同构，注释记录了从「单页五块堆叠」拆三面板的动因：正交授权维度 + 三处观测各自请求同一端点口径不一）。

**UI 实现**：三面板（profiles/switch/observability）按三组权限位拼装（48-55 行注释解释「任一档案动作即视为管理视角」的合并口径）；全无权限 → ReResult warning 显式空态（112 行）。profiles 面板 = 生效链路头条（全局开关 + 按用途激活档案一眼可见）+ RePlusPage；switch 面板 = SettingItem（AiGlobalSettingsCard.vue:74，手写 el-form 已收编进框架，重置/测试按钮与权限位由框架装配）；observability 单数据源单请求。`?tab=` 同步/懒挂载/非法 key 回落全部由 shell 承担。

**一致性与体验**：符合基准；档案行内只留在线处置（测试/激活/停用），低频与破坏性动作（探测/编辑/删除）收进抽屉动作面板并分组（aiProfileActions.ts buildAiProfileActionGroups，纯函数 + 显隐矩阵单测）。列表 fe-F-01 适用。

**问题清单**：无。

### 1.21 integration/knowledge（知识库）

**标识**：`integration/knowledge/index.vue`（34 行）+ KnowledgePanel/KnowledgeUploadDialog（174 行）+ utils/×11（含 build/sync 轮询）。

**UI 实现**：RePlusPage 列表（勾选态驱动批量启停按钮显隐，与框架批量删除同口径）+ 上传弹窗：文本（md/txt）→ FileReader 读入可编辑；二进制（pdf/docx ≤2MB）→ base64 载荷 + 「已选择文件」状态 + 编辑区禁用；解析耗时给 `submitting` info alert（117-125 行，`data-testid="knowledge-upload-parsing"`）；超限/必填校验 getPayload 返回 null 保持弹窗（89-110 行）。重建索引/删除/启停均有 useConfirm（useKnowledgeBuild.ts:34/56、useKnowledgeDocument.ts:52-53）。

**一致性与体验**：上传弹窗四态（解析中提示/读取失败 warning/校验失败 message/二进制态切换）完整；文件名自动回填去扩展名。fe-F-01 适用（列表）。

**问题清单**：无。

### 1.22 integration/ai/mcp（MCP 服务器）

薄壳页：29 行 + utils/×5 + components/×3（工具抽屉/调用日志抽屉/调用面板）。行操作内联：同步（loading 透传 row 按钮）/ 工具 / 日志（useMcpButtons.tsx:29-58，`showNumber: 6` 全内联不折叠），日志入口按 `list:McpCallLog` 显隐；新增/编辑走框架元数据表单（`useMcpServerFormOptions` 把令牌换密码框、白名单换逗号文本——敏感字段控件在装配层修正）。同步为非破坏性快照刷新，无确认合理。fe-F-01 适用。无问题。

### 1.23 integration/api-app（API 应用）

**标识**：`integration/api-app/index.vue`（137 行）+ useApiApplication（utils/×13）+ ApiUsageDrawer + 凭据弹窗（页面模板直渲染）。

**UI 实现**：顶部提示条说明一次性密钥机制（`w-99/100` + fill 底 + 图标）；凭据弹窗（fe-C-01 引用 :58，既定保留手写）：warning alert「关闭后无法再次查看」+ 三段 client_id / client_secret / callback_secret（readonly + show-password + 独立复制按钮，secret 带 `data-testid`）；`@closed` 即丢弃明文（useApiAppCredential.ts:26-29 注释「避免密钥常驻内存」）。用量抽屉（fe-C-01 引用 ApiUsageDrawer.vue:83）：1/7/14/30 天窗口 radio 切换由父级重拉、三卡汇总（失败数 danger 着色）+ 当日配额进度条（≥alert_percent 转 warning、100% exception，54-61 行）+ 三张 ReReadonlyTable 明细；数据未就绪走 ReStateContainer loading/empty（188-194 行）。密钥重置走 confirmRegenerate 确认链（useApiAppPanel.ts:69）。

**一致性与体验**：fe-C-01 两处均为「一次性结果/只读数据」语义的声明式弹层，与既定豁免口径一致。列表 fe-F-01 适用。

**问题清单**：无。

## 2. 本分册小结

### 2.1 发现统计

| 级别 | 编号 | 页面 | 一句话 |
|------|------|------|--------|
| P2 | fe-plat-P2-01 | settings/open-platform | SCIM 令牌轮换无二次确认（高危不可逆一键直达） |
| P2 | fe-plat-P2-02 | dashboard 画布 | 编辑态取消/切盘静默丢弃草稿，无 dirty 确认（对照设计器有守卫） |
| E | fe-plat-E-01 | open-platform（+settings 域） | String(error) 3 处新实例（ScimPanel.vue:96、useOAuthProviders.ts:58/78），fe-D-01 同族 |
| E | fe-plat-E-02 | integration/ai/index | 工具清单加载失败仅一次性 toast，无重试入口 |
| E | fe-plat-E-03 | dashboard 工具栏 | 编辑态/浏览态动作混排单排 wrap，「保存」主操作层级被稀释 |

计数：P2×2、E×3；P0/P1 为 0。横切引用落点：fe-F-01 标注于 ledger/login-policy/security(blockIp)/dataset/report/screen/flow/instance/leave/delegation/rule/ai-config/knowledge/mcp/api-app（15 处列表页）；fe-C-01 平台域 6 个位置、fe-C-02 平台域 5 处、fe-D-01 各在对应页引用未展开。

### 2.2 每页必查项结论

- **空数据引导**：dashboard（`dashboard.empty` + icon）、screen 投屏（noDashboards）、报表设计器（datasetMissing / noComponent / noSelection 三层）、oauth providers、AI 控制台（按入口 emptyText）、下钻抽屉（failed+retry）齐备；列表页空态由 RePlusPage/ReReadonlyTable 内建。无缺口。
- **危险操作确认**：全查 23 页——删除类（仪表盘/卡片模板/流程回滚/知识库/档案）全部有确认且危险色；**唯一缺口 = fe-plat-P2-01（SCIM 轮换）**；草稿丢弃类缺口 = fe-plat-P2-02。
- **隐藏 vs 禁用一致性**：设置域无 retrieve 隐藏页签、open-platform/instance-ongoing/message-templates 权限拼装隐藏、mcp 日志按钮 show 隐藏、login-policy 无权限整页 ReEmpty——全域一致用「隐藏 +（设置域）无权限空态说明」；禁用态均附 title 原因（报表设计器 disabledTitle 为基准）。无混用。
- **极端数据**：水印路径越界提示、短信渠道 i18n 回退、模板 content 200k / 二进制 2MB 上限、聚合字段候选交集、批量失败明细限 3 条、配额百分比 clamp 100、审批角标 max 99、下钻 total/limit 双口径——均有显式处理。
- **暗色红旗**：`#[0-9a-fA-F]{3,6}` 命中 1 处（`dashboard/components/ChartCard.vue:472` `#0b1220`，恒深色大屏骨架底、注释明确且仅 force-dark 分支生效——判定非违例）；`!important` 0 处（`w-40!` 等 tailwind 修饰符不计）。无红旗。

### 2.3 移交全栈审查

1. **fe-plat-P2-01 / fe-plat-P2-02**：均为前端独立可修（useConfirm 接入），不涉后端；建议与主册横切项同批排期。
2. **fe-plat-E-01**：建议并入 fe-D-01 的统一修复批次（同改 normalizeError 收口），避免两批改动同一批文件。
3. **fe-plat-E-02**：修复时需确认 `status()` 与 `tools()` 的重拉粒度（现 loadStatus 一并拉取，单独重试工具清单需拆分或整组重拉）——实现层小决策，前端可闭环。
4. C 级待验证（不定论）：ChartCard 内部数据失败态的表现形式（本轮按 B 级推断由组件内承担，未逐分支读 ChartCardAsync 加载链）；ReReadonlyTable 空态插画的暗色适配（screen 域已用 `--screen-*` 覆写，其余浅色面板无风险）。

### 2.4 Top 发现

1. **fe-plat-P2-01**：SCIM 令牌轮换无确认——平台域唯一「高危不可逆操作一键直达」，5 行修复消除断连生产集成风险。
2. **fe-plat-P2-02**：仪表盘草稿静默丢失——同域报表/大屏设计器已有 dirty 守卫基准，本页补齐成本低、收益直接。
3. **fe-plat-E-03**：仪表盘工具栏信息层级——随 P2-02 一并处理可共享「编辑态动作收纳」的改动面。

### 2.5 已读文件（证据源）

入口页 23 个见 §0 表「入口组件」列（src/views/ 下）。支撑组件与 utils（约 35）：settings-shell/{index,SettingItem}.vue、settingAuth.ts、WatermarkSetting.vue、MessageTemplatePanel.vue、ScimPanel.vue、OAuthProvider{Panel,Card}.vue、AuthorizedAppAdminPanel.vue、DashboardToolbar.vue、CardHeaderActions.vue、CardDrillDrawer.vue、useDashboardEditing.ts、useDashboardDelete.ts、datasetPreview.ts、ChartCard.vue（样式段）、DesignerToolbar.vue、FreeCanvasToolbar.vue、TemplateVersionsDialog.vue、ScreenControlForm.vue、useScreenPersistence.ts、approvalRowButtons.ts、Approval{Panel,Stats,ResourceTabs}.vue、instanceRowButtons.ts、useInstanceBatchActions.tsx、instanceBatchOutcome.ts、instanceFormDialog.tsx、instanceDialogs.tsx、InstancePanel/InstanceDetail.vue、FlowVersions.vue、FlowCanvas.vue、useFlowDrawers.tsx、ApprovalRuleImpactField.vue、delegationToggle.ts、useMcpButtons.tsx、useKnowledgeBuild/Document.ts、useApiAppCredential/Panel.ts、aiProfileActions.ts、AiChatPanel.vue（头部）、AiGlobalSettingsCard.vue、KnowledgeUploadDialog.vue、ApiUsageDrawer.vue、ledger hook.tsx。
