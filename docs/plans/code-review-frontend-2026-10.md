# 2026-10 前端专项审查 · 主册

> 只读审查产物：布局 / 主题 / 组件 / 状态与数据流 / 工程化 / 交互一致性（横切 A–F）+ 逐页 UI。
> 功能正确性归 [Phase 1](code-review-2026-10.md)（引用其编号，不重复展开）；本审查发现的functional问题记入 §6 移交全栈审查。
> 问题编号 `fe-<级别>-<序号>`，只在审查文档内使用。视觉结论以运行时为准（本审查静态，未跑浏览器）。

## 0. 审查说明

- **范围**：`xadmin-client/src/` 全量横切（layout / style / components / hooks / store / api / utils / router / plugins）+ 67 个叶子页逐页 UI（口径：`src/views/` 叶子目录 + 静态路由页，error 三态按 1 组计，复用组件页去重）。
- **事实源**：源码 > 契约镜像 > 文档/注释；样式引擎实为 **Tailwind CSS v4**（`src/style/tailwind.css` `@theme` 桥接语义令牌，`src/style/tokens/`；UnoCSS 已全量移除——`uno.config.*` 不存在，Phase 1 已证）。
- **证据等级**：A=代码直接确证；B=跨文件静态推断；C=待运行时/视觉验证。
- **量化口径**：全部给出目录范围 + grep 正则 + 排除项，可复算；只读门禁脚本（check:i18n / check:color-literals 等）的输出可直接引用（不产生构建产物）。
- **审查基线**：xadmin-client `d015b6db` + 暂存 14 文件（dashboard/dataset、ai-mcp、ledger、logs 等页面改动，未提交）；审查以工作区现状为准。**注意：审查期间存在并行修改窗口，各结论以对应文件被读取时点的内容为准。**
- **执行方式**（用户授权 ≤3 子代理，解除原 PROMPT 单代理限制）：第一波 3 个子代理并行横切 A–F；第二波 3 个子代理并行逐页三分册；主册由主会话汇编。

## 1. 总览统计（滚动更新）

| 波次 | 内容 | 状态 |
|---|---|---|
| 1 | 横切 A 布局 / B 主题 / C 组件 / D 状态数据流 / E 工程化 / F 交互一致性 | ✅ 已完成（3 子代理并行） |
| 2 | 逐页三分册：pages-system（30 页）/ pages-platform（23 页）/ pages-other（14 页） | ✅ 已完成（3 子代理并行） |
| — | 量化指标附录 / 行动建议 / 移交全栈审查 / 未验证事项 | ✅ 已完成 |

### 1.1 覆盖率与问题统计（最终）

**覆盖率**：横切 6/6 项 + 逐页 **67/67**（系统域 30 / 平台域 23 / 其他域 14；口径：`src/views/` 叶子目录 + 静态路由页，error 三态按 1 组、复用组件页去重）。全部 A 级证据（逐文件读源码），未跑构建/浏览器。

| 级别 | 横切 | 逐页 | 合计 | 说明 |
|---|---|---|---|---|
| P0 | 0 | 0 | **0** | 无功能错误/高危安全缺陷 |
| P1 | 0 | 0 | **0** | 无项目约定违反（各门禁基线内） |
| P2 | 15 | 11 | **26** | 横切 fe-A×3/B×1/C×4/D×2/E×3/F×2；逐页 fe-sys×3/fe-plat×2/fe-other×6 |
| E | 6 | 10 | **16** | 增强建议（均给价值/成本/依赖） |

C 级待验证 6 项记 §7。**框架级发现 1 项升级**：fe-sys-P2-01（无 list 权限的 RePlusPage 页整页空白无解释，影响全站约 17 页，与 fe-F-01 同为 RePlusPage 缺内联状态族）。

**Top 5 收益最高的改进**（每条一句话 + 证据）：
1. **RePlusPage 补「无权限 denied + 加载失败 retry」两个内联态**（fe-sys-P2-01 + fe-F-01）——一处框架改动收敛全站约 37 个列表页的权限态/失败态缺口，是本审查性价比最高的单项。
2. **弹层选型收口**（fe-C-01）：14 处声明式弹层绕过 addDialog 建基线门禁 + useConfirm 扩 prompt/alert（fe-C-02）——滚动弹层三套实现回一。
3. **错误归一统一 normalizeError**（fe-D-01 + fe-plat-E-01 + fe-sys 坐标 6 处）：11+3 处 String(error)/同构手写残余一批清偿。
4. **聊天室流式与上传反馈**（fe-other-P2-01/02/03）：流式期间丢消息、上传零反馈、@联想无键盘支持——IM 可用性三连修。
5. **仪表盘编辑态 dirty 守卫**（fe-plat-P2-02）：取消/切盘静默丢草稿，复用 useDesignerGuard 基准即可。

## 2. 横切 A–F（第一波 3 子代理并行结果汇编）

> 执行方式：3 个子代理并行（A+B / C+D / E+F），编号已归一为 `fe-<域>-<序号>`；两个代理独立发现的同一问题（声明式弹层）已合并到 fe-C-01，F 节引用。子代理证据已抽核（hook-length 红灯经主会话复跑确认，见 fe-E-01 备注）。

### A. 布局体系

**结论**：共 7 种布局模式，以语义谓词替代子串判定（`src/layout/hooks/layoutModes.ts:19-36`），移动端强制回落 vertical（:156-161），窄屏临时回落只改 body 属性不落库防偏好覆写（`src/layout/index.vue:285-291`）；侧栏状态机互斥处理完备（hover 展开三重门控且切换即复位 `useSidebarHoverExpand.ts:32-61`；pinned 强开侧栏 `useSidebarPinned.ts:22-28`；顶栏自动隐藏失效即复位 `useHeaderAutoHide.ts:109-115`）；响应式健全：JS/SCSS 断点同源有用例防漂移（`src/utils/breakpoints.ts:12-19` ↔ `tokens/breakpoints.scss:19-26`），≤md 移动抽屉+遮罩+inert 防焦点逃逸（`index.vue:250-252`），溢出三重防护（`index.vue:440`、`lay-content/index.vue:271-277`、`element-plus.scss:38-40` 弹窗 92vw 钳制）。面包屑仅在「顶栏无菜单」模式渲染，horizontal 模式由 NavHorizontal 自渲染且无面包屑——`breadcrumbVisible` 开关在该三态天然无效（设计取舍，非缺陷）。

| 编号 | 级别 | 证据 | 影响面 | 建议 | 工作量 | 等级 |
|---|---|---|---|---|---|---|
| fe-A-01 | P2 | `layout/components/lay-frame/index.vue:27-41` 清理逻辑被 `keep.value` 门禁 | 关掉的 iframe 页签组件与远端资源常驻 DOM/内存，直到下次进入 iframe 路由且 tags 再变化才自愈 | 清理移出门禁（cleanup 不依赖当前路由）或关签时同步 delMap | S | A |
| fe-A-02 | P2 | `NavVertical.vue:236,279-290` | 双列布局叶子菜单态折叠按钮随主列消失，无鼠标折叠入口（仅剩 Alt+S） | 折叠按钮渲染条件与主列 v-if 解耦 | S | A |
| fe-A-03 | P2 | `index.vue:162-167` + `store/modules/app.ts:66-72` | full-content 下 Alt+S 仍写持久化折叠态，切回其他布局时侧栏意外收起 | isFullContent 时短路 toggleSideBar | S | B |
| fe-A-E01 | E | `index.vue:71,280-281` | UA-mobile 下 ResizeObserver 早退，viewportSize 永不更新（现仅 SettingStretch 消费） | 补同步或注释取舍 | S | B |
| fe-A-E02 | E | `router/utils/nav.ts:27-38` + `permission.ts:69-86` | 同 name 多开路由（dynamicLevel）关一签清同名全部缓存；关后台签对当前路由走 delete→100ms re-add 窗口，窗口内导航丢组件态 | 关签路径改传被关标签 name 或登记为已知限制 | M | B |

### B. 主题与外观

**结论**：亮暗链路完整分层（bootTheme 兜底 `src/utils/bootTheme.ts:1-18` → EP 官方暗色变量 → 语义槽 `:root`/`html.dark` 成对 `tokens/semantic.scss:83` → ep-bridge 同特异性再声明 `tokens/ep-bridge.scss:36-40`），全量扫描未见「只写亮色」孤点；Tailwind v4 引擎与命名一致（`tailwind.css` 全 v4 语法，依赖 `tailwindcss@^4.3.3`，无 uno.config）；外观档位逐档核对全页面生效（uiSize→`el-config-provider :size` App.vue:90-91；compactMode→`--content-gap`；圆角/字号→`html[data-radius]`/`[data-font]`；色弱/半暗→html 类；siteWideAppearance 9 键与后端键位一一对应 `siteWideAppearance.ts:11-24`）；样式组织健康（login 现状为 `src/style/login.scss` 135 行被 login/index.vue @use 引入，无孤儿 css；sidebar partial 同名类均在 `body[layout=…]` 作用域下非重复）。

| 编号 | 级别 | 证据 | 影响面 | 建议 | 工作量 | 等级 |
|---|---|---|---|---|---|---|
| fe-B-01 | P2 | `element-plus.scss:16-19` `.is-dark{z-index:…!important}` + `layout/tippy.ts:1-3` | 注释意图是 tippy popper，但 tippy 不产 `.is-dark` 类——实际命中 EP 全部暗色弹层，9999 压过弹窗/遮罩层叠 | 选择器收敛到 tippy 实际主题类或压低层级 | S | A |

**量化**：硬编码色自查口径 36 处（style 13 / views 11 / components 10 / layout 2，均属色板定义/取色器等合法场景）；官方门禁 `check:color-literals` 实跑「16 文件/94 处，基线只减不增，通过」。`!important` 117 处（style 84 / views 14 / components 13 / layout 5 / assets 1），抽样取值全部为 `var(--*)` 令牌——未违反「暗色走令牌」约定，属与 EP 打特异性的可控样式债（仅 fe-B-01 一处例外）。`sidebar/_common.scss` 746 行偏肥可再拆（P2 级以下，不单列编号）。

### C. 组件体系

**清单汇总**（口径：`grep -rl "components/<Name>" src` 排除组件自身目录，含 spec）：**62 个一级组件，Re\* 43（69%）/ 领域 19（31%）；零引用 0；有测试 47（76%）；无测试 15**（AuthorizedAppTable、DataTable、ReAnimateSelector、ReCol、ReCropper、ReImageVerify、ReMenuPermissionTree、ReMfaConfirm、RePictureUpload、ReReadonlyTable、ReSendVerifyCode、ReSplitPane、ReTaskCenterDrawer、ReTreeLine、SearchPicker）。核心组件引用数：RePlusPage 192 / ReDialog 90 / ReIcon 88 / ReEmpty 56 / ReReadonlyTable 27 / ReDrawer 23 / DataTable 17。四表边界清晰（RePlusPage=元数据列表页 / DataTable=页面级手写表壳 / ReReadonlyTable=只读少列独立分页 / RePureTableBar=RePlusPage 内建工具栏）；ReDialog/ReDrawer 已共享 `src/components/overlay/` 内核（滚动锁/页脚/尺寸单源），addDialog 52-54 处、addDrawer 20 处收口。

| 编号 | 级别 | 证据 | 影响面 | 建议 | 工作量 | 等级 |
|---|---|---|---|---|---|---|
| fe-C-01 | P2 | 声明式 `<el-dialog>/<el-drawer>` 绕过 addDialog/addDrawer：视图层 14 处（OAuthProviderPanel.vue:139、FormFieldOptionsDialog.vue:153、TemplateVersionsDialog.vue:86、api-app/index.vue:58、ApiUsageDrawer.vue:83、dataset/index.vue:52〔注释明示既定保留〕、CardDrillDrawer.vue:95、MemberDrawer.vue:118、AiDraftDialog.vue:117、ScopeSelect.vue:157、UserMfaPanel.vue:389、LoginPrivacyNotice.vue:21、Register.vue:160、SearchModal.vue:319）＋封装本体/全局件豁免 5 处 | 弹层滚动锁/页脚/尺寸三套实现并存；同页混用两种弹层路径；表单维度有 check:forms 选型门禁而弹窗维度没有，无防扩散机制 | 仿 check-bare-tables 建声明式弹层基线门禁（只减不增），或在 component-handbook 补「弹窗选型判定」 | M | A |
| fe-C-02 | P2 | ElMessageBox prompt/alert 直用 18 处/15 文件（designerTemplate.ts:65、templateToolbar.ts:94,114、SavedPlans.vue:54,69、useMenuRename.ts:27、UserPasskeyPanel.vue:53、OAuthBindings.vue:83、approvalRowButtons.ts:44、FlowVersions.vue:72、ScimPanel.vue:54 等） | prompt/alert 形态无统一封装，标题/按钮/样式各写各的（二值 confirm 已收口 useConfirm） | 扩 useConfirm 家族补 prompt/alert（或 useMessageBox），分批收敛 | S-M | A |
| fe-C-03 | P2 | emit 命名混用（ReScrollbar `scroll-at` kebab vs ReApiSelect `optionsChange` camel）；v-model 三形态并存（defineModel / defineModel("value") / options API 手写 update:modelValue，ReSegmented/src/index.tsx:63） | 消费端需记忆两套写法 | components/README §六落约定（emit camel + 新组件统一 defineModel），存量不强制返工 | S | A |
| fe-C-04 | P2 | 无单测 15 组件中：ReReadonlyTable（27 文件引用）、DataTable（17）、ReMfaConfirm（MFA 安全件）、ReSendVerifyCode（登录链路） | 核心壳组件回归无单测兜底，行为靠 E2E | 优先补 ReReadonlyTable/DataTable/ReMfaConfirm 用例 | M | A |
| fe-C-E01 | E | ReSliderCaptcha 仅 playground 引用（README 已声明保留）、ReTableAction 仅 FlowVersions 直用 | 关系不直观（非缺陷） | 清单标注 playground-only / 渲染器共用关系 | S | A |
| fe-C-E02 | E | MessageThreadPanel 15 props 2 消费方（types.ts:7-30 单源+README 文档化） | 骨架透传偏多，属刻意设计 | 再增 props 时改 config 对象或拆插槽 | S | A |

**量化**：`<el-table` 直用 11 处 = `scripts/bare-tables-baseline.json` 基线 11 条，**新违例 0**（`check:tables` 门禁只减不增）；ElMessage 约定外直用 **0**（仅 `plugins/elementPlus.ts:104` 注册与 `hooks/useConfirm.ts:5` 收口本体）。

### D. 状态与数据流

**结论**：10 个 store 全部 <500 行无上帝 store（最大 siteConfig 442 行职责内聚；user 252/notice 230/multiTags 186/permission 98/app 94/loginPage 64/watermark 58/epTheme 49/settings 48）；store↔api 模块环已用动态 import 破除（`check:module-cycles` 守护）。api 层 66 文件中 65 个走 BaseApi 家族；错误归一已升级为 `normalizeError`（src/utils/apiError.ts，**76 处采纳**）；fetch/axios 逃逸仅 2 处且合法（versionCheck.ts:18 启动探测、sse.ts:139 原生流）。**手写分页散落 0**（`const pageNo = ref(` views 全域 grep 为 0——Phase 1 线索中的「hooks 已有页面手写」重灾区假设再次证伪）。

| 编号 | 级别 | 证据 | 影响面 | 建议 | 工作量 | 等级 |
|---|---|---|---|---|---|---|
| fe-D-01 | P2 | 手写归一残留 11 处/8 文件：useAppearancePackActions.ts:77,89,99、ScreenControlForm.vue:64,82 用旧 `String(error)` 形态（对象错误 → "[object Object]"）；McpCallLogsDrawer.vue:52、AiDraftDialog.vue:61 与 normalizeError 同构重复；SavedViews.vue:108 自造 fallback（SchemaHistoryDialog.vue:58 为已注释豁免） | 对象错误 detail 不可读；归一双轨 | 统一换 `.catch(normalizeError)` | S | A |
| fe-D-02 | P2 | 14 个 hook 无 spec（passkeyAssert、passkeyRegister、mfaVerifySubmit、useSaveShortcut、useHistoryPagination、useMessageCollection 等） | passkey/MFA 提交链等纯逻辑仅靠消费方测试间接覆盖 | 优先补 passkeyAssert/useSaveShortcut/useHistoryPagination | M | A |
| fe-D-E01 | E | `store/modules/notice.ts:67` store 内直接开弹窗+调 batchRead | store 承载 UI 动作越层（已有注释动机） | 移 utils 层 dialog helper | S | A |
| fe-D-E02 | E | fetch 逃逸 2 处无登记口径 | 后人可能模仿 | README 注明「仅启动探测与 SSE 允许原生 fetch」 | S | A |

**hooks 复用率**（外部 import 文件数）：useConfirm 56、useDialogForm 18、useSortable 7、usePagedList 5、useListAppendAnimation 5、useSplitPaneConfig 4、useFullHeightPanel/useDesignerGuard/useMessageCollection 3、useFormScrollToError 等各 2、useScrollLock 等各 1；**零消费 hook = 0**（内部工具 hook 间消费非死码）。注：usePagination 已并入 ReIcon/src/usePagination.ts（注释禁新引用）、useECharts 实为 plugins/echarts.ts——Phase 1 线索中的两个 hook 名不存在于 hooks/。

### E. 工程化质量

**结论**：类型质量优秀——`any` 仅 3 处（均 `emit: (...args: any[])`）、`@ts-ignore/@ts-expect-error` 0、`as unknown as` 双重断言已全量清零（check:as-unknown 基线空，实跑通过）；路由懒加载 100%（静态路由全 `() => import()`、动态路由懒 glob + `resolveComponentKey` 精确优先解析）；守卫单点收口（router/index.ts:104-250 + PUBLIC/GUEST_ONLY 白名单分离，动态路由失败 500 兜底防循环）；i18n 完全对称（`check-i18n-keys` 实跑：zh 4835 / en 4835，静态引用 3427 key）；体积治理体系化（预算制门禁 15KB/窗口双账本，基线闭包 470.1KB gzip、主 chunk 69.4KB，docs/perf-firstscreen.md 台账 567.5→432.6KB -23.8%）；echarts 按需懒加载（plugins/echarts.ts 动态 import + 按需注册）；manualChunks 仅 vue-core/echarts 两组（注释记录「EP 分组拉回首屏 699→1203KB」实测教训）。

| 编号 | 级别 | 证据 | 影响面 | 建议 | 工作量 | 等级 |
|---|---|---|---|---|---|---|
| fe-E-01 | P2→**已修复** | 子代理实测 `check:hook-length` 失败（useUserPanel.ts 函数 124 行>120）；主会话复核时门禁已通过（0 超长），`git diff` 显示并行窗口未提交改动（3+/6-）已清偿 | 无（已修复） | 无 | — | A |
| fe-E-02 | P2 | useDialogForm 仅 15 文件采用；手写 beforeSure 提交点约 18 处；useDialogForm docstring 自述手写样板两类缺陷（校验失败漏 closeLoading / 未 catch 致 loading 悬挂，src/hooks/useDialogForm.ts:5-9） | 弹窗提交防重复/loading 悬挂依赖逐处自觉 | 滚动迁移 useDialogForm（与 fe-F-02 联动） | 滚动 M | B |
| fe-E-03 | P2 | `check:success-code` 实跑：`code === SUCCESS_CODE` 字面量存量 1 处/1 文件（基线冻结中） | isSuccess 单一真源迁移尾巴 | 迁移后 --update 收敛 | S | C |
| fe-E-04 | P2 | 硬编码中文 74 处（剔除注释/测试口径）：约 70 处为 console 告警与 playground 演示文案（豁免合理）、1 处语言原生名（localeOptions.ts:3）、**用户可见仅 1 处**（ReTypeit/src/index.tsx:38） | en 语言下 1 处用户可见中文 | ReTypeit 一处进词条；playground 登记豁免 | S | C |

**量化**：any 3 / @ts-ignore 0 / as-unknown-as 0 / 单重 as 断言 750 处（抽 5 处均为边界必要收窄，未见滥用）；硬编码中文 74（用户可见 1）；懒加载覆盖 100%（router 注册页面静态 import 0）；门禁实跑：i18n / as-unknown / template-root / file-length / entry-closure / bare-tables / handwritten-forms / quarantine / success-code **通过**，hook-length 复核时通过。

### F. 交互一致性与可访问性

**结论**：反馈封装高度收口——ElMessage 直用 0（仅 message.ts 本体），`message()` 约 540 调用点/213 文件且扩展 keyedMessage/withKeyedLoading 串联反馈（message.ts:112-167）；删除全链路有确认（views 内直接 `api.destroy` 仅 5 处且调用方全部有 confirm；RePlusPage 删除走 confirmImpact 影响确认，utils/impact.ts:112-115）；快捷键无冲突（6 个全局注册点随挂载/卸载、语义域隔离、输入态守卫）；a11y 有门禁护航（axe E2E 9 场景、ALLOWED_VIOLATIONS 空表、表格可访问名 utils/tableA11y.ts 单源、焦点归还依赖 EP focus-trap 且有单测+E2E 守护）。

| 编号 | 级别 | 证据 | 影响面 | 建议 | 工作量 | 等级 |
|---|---|---|---|---|---|---|
| fe-F-01 | P2 | 列表页 error 态为「http 层 toast + 表格停在空态」：`RePlusPage/src/utils/plusPageRequest.ts:119-126` 注释自证「失败的提示由 http 层统一给出，此处只收尾 loading」；ReStateContainer 的 error/denied 态 views 仅 4 文件使用 | 失败后的空表与真空数据不可分辨（用户有 toast，属平台一致的设计取舍——但缺可恢复入口） | RePlusPage 增加「加载失败→重试」内联态（复用 ReStateContainer） | M | B |
| fe-F-02 | P2（C 级） | 手写 beforeSure 约 18 个提交点中 ~5 文件无 closeLoading/finally/catch 字样（KnowledgeUploadDialog、AccessTokenCreateForm、DatasetForm、FormLinkageSection、CardForm）——未逐处精读上层兜底 | 个别弹窗提交异常 loading 悬挂风险 | 逐处精读确认或统一迁移 useDialogForm（fe-E-02）；先记 §7 待验证 | S | C |
| fe-F-03 | P2 | aria-label 123 处但 RePlusPage 驱动的列表页 index.vue 自身 0 处——语义由 EP 组件+tableA11y+axe 兜底；a11y E2E 仅覆盖 9 场景 | 未覆盖页面无自动化 a11y 保障 | a11y 扩面优先表单密集型页面 | 滚动 | C |
| — | — | 弹层双路径 21 处（16 内联+5 豁免，逐处判断均为常驻面板/只读展示型可接受） | 已合并至 **fe-C-01**，此处不重复编号 | — | — | — |

**四态覆盖率**（分母：src/views 下 65 个 index.vue）：loading ≈95%+（RePlusPage 统一 `:loading`，壳页/演示页不适用）；empty 覆盖良好（pure-table 内置 + ReEmpty 8+ 页显式）；**error 态缺失 = 全部 RePlusPage 直驱列表（约 37 页）**（fe-F-01，唯一系统性缺口）；无权限 denied 内联态仅 3 页显式（其余由按钮级权限位门控承载）。防重复提交抽 8 处 **8/8 有收口**（instanceFormDialog closeLoading、userResetPassword finally、useReportDialogs useDialogForm 等）或走 useDialogForm 内置防线（有单测钉住）。

## 3. 逐页 UI（第二波 3 子代理并行，分册已落盘）

| 分册 | 页数 | P2 | E | 覆盖率 | Top 发现 |
|---|---|---|---|---|---|
| [系统域](code-review-frontend-2026-10-pages-system.md) | 30 | 3 | 5 | 30/30 | fe-sys-P2-01 无 list 权限整页空白（框架级）；menu 树失败伪装空态；codegen 四处静默失败 |
| [平台域](code-review-frontend-2026-10-pages-platform.md) | 23 | 2 | 3 | 23/23 | SCIM 令牌轮换无二次确认；仪表盘编辑态丢草稿 |
| [其他域](code-review-frontend-2026-10-pages-other.md) | 14 | 6 | 2 | 14/14 | chat 流式丢消息；上传零反馈；form/my 摘要列无收敛 |
| 合计 | **67** | **11** | **10** | **100%** | 7 页零问题（form/data、user/notice、oauth×2、invite、about、error） |

逐页结论概要：四态与权限态在系统域呈「4 页显式 denied 态 + 17 页框架缺省」双轨（fe-sys-P2-01）；危险操作确认 23 页平台域仅 SCIM 1 处缺口、隐藏 vs 禁用全域一致；暗色红旗零新增（hex 命中均为功能性回退且有注释）；`onBeforeRouteLeave` 全域 0 命中（fe-sys-E-01：module/codegen 草稿型页面建议补离开确认）。invite/accept 与 account/Preferences 为状态机与敏感态处理正面样板。

## 4. 量化指标附录（口径随条目）

| 维度 | 数值 | 口径 |
|---|---|---|
| 组件总数 / Re\* 占比 | 62 / 69%（43） | src/components 一级目录，grep import 引用计数 |
| 零引用组件 / 无测试组件 | 0 / 15（76% 有测试） | 同上 + __tests__ 与 *.spec 探测 |
| `el-table` 直用 | 11 处 = 基线 11 条，违例 0 | `<el-table` grep vs scripts/bare-tables-baseline.json |
| ElMessage 约定外直用 | 0（message() 540 调用点/213 文件） | 排除 message.ts/useConfirm 本体 |
| ElMessageBox prompt/alert 直用 | 18 处/15 文件（fe-C-02） | 二值 confirm 已收口 useConfirm（56 文件采用） |
| 声明式 el-dialog/el-drawer | 视图层 14 + 豁免 5（fe-C-01） | grep `<el-dialog\|<el-drawer` src |
| Pinia store | 10 个全部 <500 行，无上帝 store | 最大 siteConfig 442 行 |
| 手写分页散落 | 0 | `const pageNo = ref(` views 全域 grep |
| normalizeError 采纳 / 手写归一残留 | 76 处 / 11 处/8 文件（fe-D-01） | grep 两种形态 |
| `any` / @ts-ignore / as unknown as | 3 / 0 / 0 | 排除 *.d.ts；check:as-unknown 基线空 |
| 单重 as 断言 | 750 处（抽 5 处均为必要收窄） | `as [A-Z]` 非 spec grep |
| 硬编码中文 | 74 处，用户可见 1（fe-E-04） | 剔除注释/测试/playground 单列 |
| i18n zh/en | 4835/4835 完全对称，静态引用 3427 key | node scripts/check-i18n-keys.mjs 实跑 |
| 路由懒加载 | 100%（静态 import 0） | router 注册面 grep |
| 硬编码色 / !important | 门禁口径 94 处（基线内）/ 117 处（全令牌取值） | check:color-literals 实跑 + grep 抽样 |
| 四态覆盖率 | loading ≈95%+ / empty 良好 / **error ≈0（列表页）** / denied 内联 3 页 | 65 个 index.vue 分母（fe-F-01、fe-sys-P2-01） |
| 防重复提交抽检 | 8/8 有收口或 useDialogForm 内置防线 | 表单提交点抽样 |
| 体积基线 | 闭包 470.1KB gzip（代码 404.9+i18n 65.2）、主 chunk 69.4KB；历史 -23.8% | check-bundle-size 基线（2026-10-20）+ docs/perf-firstscreen.md；本轮未构建 |

## 5. 行动建议（并入 DEV-TASKS 候选）

| 优先级 | 编号 | 建议 | 涉及文件 | 工作量 | 验证方式 | 回归风险 |
|---|---|---|---|---|---|---|
| 1 | fe-sys-P2-01 + fe-F-01 | RePlusPage 补「无 list 权限 denied 态」与「加载失败 retry 态」（复用 ReStateContainer），收敛全站列表页 | `RePlusPage/src/index.vue:261`、`utils/plusPageRequest.ts:119-126` | M | E2E：无权限账号访问列表页见解释态；断网后见重试态 | 中：全站列表页渲染路径，需双浏览器回归 |
| 2 | fe-C-01 | 声明式弹层建基线门禁（仿 bare-tables 只减不增）+ component-handbook 补弹窗选型判定 | `scripts/` 新门禁 + 14 处存量登记 | M | 门禁跑通 + 存量清点 | 低 |
| 3 | fe-other-P2-01/02 | chat 流式期间发送入队/禁用 + 附件上传进度反馈 | `ChatWindow.vue:218-224`、`useChatStreaming.ts:53` | S-M | E2E 聊天 spec | 低 |
| 4 | fe-plat-P2-01 | SCIM 令牌轮换补 useConfirm danger 确认 | `ScimPanel.vue:85-100` | S | 手工+E2E | 低 |
| 5 | fe-plat-P2-02 | 仪表盘编辑态复用 useDesignerGuard dirty 守卫 | `useDashboardEditing.ts:44-55`、`index.vue:78` | S | E2E dashboard | 低 |
| 6 | fe-B-01 / fe-A-01/02/03 | 三处布局/主题小修（is-dark z-index 收敛、iframe 缓存清理出门禁、双列折叠入口、full-content 快捷键短路） | 见横切表 | 各 S | 视觉基线 + 手工 | 低 |
| 7 | fe-D-01 + 各分册坐标 | String(error)/同构手写归一统一批清偿（含 plat 3 处、sys 6 处新坐标） | 见 fe-D-01 与分册坐标 | S | 既有单测 + grep 复算 | 低 |
| 8 | fe-C-02/03/04、fe-D-02、fe-E-02/03/04 | 滚动收敛类：useConfirm 扩 prompt/alert、emit/defineModel 约定落 README、核心组件与 passkey hooks 补测试、useDialogForm 滚动迁移、success-code 尾巴、ReTypeit 词条 | 见各条 | 滚动 | 各门禁 + 新增 spec | 低 |

## 6. 移交全栈审查（功能/权限/契约正确性，Phase 1 口径）

1. **chat 流式并发语义**（fe-other-P2-01 后端面）：流式期间消息的入队/丢弃语义需后端会话序号配合——Phase 1 form-chat 分册未覆盖此交互维度，建议全栈侧补一条并发守护测试。
2. **字段删除影响面回传**（fe-other-P2-04 关联）：designer 删除字段对存量提交数据的影响目前仅前端脏检查兜底，后端 rollback/影响面端点（dynamic-forms 已有 rollback）可考虑回传受影响提交数。
3. **字段值文本化协议统一**（fe-other-P2-05）：form/my 摘要列与 form/data fieldValueText 的 JSON→文本化口径漂移，属元数据协议消费侧差异，建议在 `docs/architecture/metadata-protocol.md` 登记单一口径后两侧对齐。
4. **playground 词条豁免登记**（fe-E-04 口径冲突）：playground registry 约 42 处中文 description 是否登记门禁豁免需维护者决策（other 分册 C 级项）。
5. 其余逐页坐标补充（fe-C-02 ×5、fe-D-01 ×6 新位置）已回写各分册，无需另立条目。

## 7. 未验证事项（C 级，静态审查边界）

1. **fe-F-02**：~5 处弹窗提交是否缺 closeLoading/finally（KnowledgeUploadDialog、AccessTokenCreateForm、DatasetForm、FormLinkageSection、CardForm）——需逐处精读或运行时验证。
2. **playground 中文口径**：fe-E-04「用户可见 1 处」与 other 分册「registry 42 处」的豁免范围需维护者确认（§6.4）。
3. **视觉/暗色实渲染**：全部暗色与布局结论为静态核对（令牌链路完整），实际渲染效果待视觉基线（`pnpm test:e2e:visual`）。
4. **体积当前值**：本轮按铁律未 build，引用的是 2026-10-20 基线；当前工作区体积待下次构建取值。
5. **a11y 全量**：axe E2E 覆盖 9 场景，未覆盖页面的实际可达性待扩面。
6. **审查期间并行窗口**：client 工作区存在未提交改动且审查期间持续变化（hook-length 红灯在会话内被修复即为例证）——各结论以对应文件被读取时点为准，提交前建议以最终工作区复跑 `pnpm lint && pnpm typecheck && for g in … do pnpm check:$g; done` 全量门禁。

## 8. 整改执行状态（2026-10-26）

| 编号 | 状态 | 落地内容 / 证据 |
|---|---|---|
| fe-sys-P2-01 + fe-F-01 | ✅ | RePlusPage 新增 `PageState`（denied/error+retry）与 `statePolicy`（默认 framework）；错误原因由请求层 `loadError` 提供；全站列表页不再「无权限整页空白 / 失败只剩空表」 |
| fe-C-01 | ✅ | `check:overlays` 基线门禁（21 文件，只减不增）+ `handbook-frontend` 弹层判定 |
| fe-C-02 | ✅ | 新增 `useMessageBox`（`usePrompt` / `useAlert` / `closeMessageBox`）；15 处 prompt/alert 已迁移（模板/设计器/审批/账号/开放平台/调度引用等），视图层直用清零；保留三态 confirm 例外 |
| fe-C-03 | ✅ | `components/README.md` §七：emit camelCase、`defineModel` 统一、弹层/确认选型 |
| fe-C-04 | ✅ | 补 DataTable / ReReadonlyTable / ReMfaConfirm（并发 412 去重与取消）单测 |
| fe-D-01 | ✅ | 11+ 处 `String(error)` / 手写归一改 `normalizeError`（含 codegen、monitor、menu、SCIM、OAuth provider、screen、MCP、AI 草案、SavedViews 等）；`SavedViews` 自造 fallback 收敛 |
| fe-D-02 | ✅ | 补 `passkeyAssert` / `useSaveShortcut` / `useHistoryPagination` 单测 |
| fe-E-02 useDialogForm 滚动迁移 | ⏳ 滚动 | 与 fe-F-02 联动，按改动面继续迁移；本轮未批量改 18 个提交点 |
| fe-E-03 success-code | ✅ | 登录页 `res.code === 1000` → `isSuccess`；基线仅剩 `api/types.ts` 的判定实现本体 |
| fe-E-04 ReTypeit 词条 | ✅ | ReTypeit 错误文案入 `login.typeItSingleElement`（zh/en）；playground 注册表中文保留（开发用非生产路径，待维护者决定是否入词条） |
| fe-A-01 | ✅ | iframe 缓存清理移出 `keep` 门禁（关闭页签即回收） |
| fe-A-02 | ⚠️ 维持 | 复核为设计取舍：双列「窄列独显」态侧栏已是窄宽，CSS 明确保持窄宽（无次级列可折叠），折叠按钮隐藏非缺陷；未改 |
| fe-A-03 | ✅ | full-content 下 Alt+S 短路，不再写持久化折叠态 |
| fe-A-E01/E02、fe-B-01 | ✅ | fe-B-01 `.is-dark` 收敛为 `.tippy-box[data-theme="dark"]`；其余维持登记 |
| analysis-E-01 | ✅ | 联动重拉轻量指示（同主册行动项） |
| 24 处 ElMessageBox（fe-C-02 台账） | ✅ | 视图层 prompt/alert 清零；剩余 `ElMessageBox.confirm` 为三态确认（README 已登记例外） |

> 未完成项：fe-E-02（useDialogForm 滚动）、playground 42 处中文（维护者决策）。其余横切 P2/E 均按上文落地；验证口径见主册与后端整改进度。

## 9. 第二轮收口（2026-10-26）

| 编号 | 状态 | 落地内容 |
|---|---|---|
| fe-sys-P2-02 | ✅ | 菜单树 `loadError` + ReStateContainer 错误态/重试，失败不再伪装空树；补 3 例单测 |
| fe-sys-P2-03 | ✅ | 代码生成 models / plan / preview / download 四处失败显式错误态 + 重试；错误展示抽 `CodegenLoadError` |
| fe-sys-E-01 | ✅ | 模块裁剪与代码生成页接入 `useDesignerGuard`（dirty 基线 + 离开确认） |
| fe-sys-E-02 | ✅ | monitor / HealthBanner / UserTree 的 Tailwind 原色改 EP 令牌 |
| fe-sys-E-04 | ✅ | 下载中心导出页签补 `list:SystemExportRecord` 权限判断（与导入页签对称） |
| fe-plat-E-02 | ✅ | AI 工具清单失败显式提示 + 重试入口（不再静默消失） |
| fe-plat-E-03 | ✅ | 仪表盘编辑态「保存 / 删除」间加分隔，危险动作与主操作分层（不再整体重排工具栏） |
| fe-other-P2-03 | ✅ | @ 候选支持上下键循环 / Enter 选中 / Esc 关闭，候选打开时 Enter 不再误发送；补 2 例单测 |
| fe-other-P2-04 | ✅ | 设计器字段删除加 danger 确认（提示公式/联动影响） |
| fe-other-P2-05 | ✅ | 摘要列复用统一文本化：附件名、明细行数、日期区间、长度截断 + title 全文；补 6 例单测 |
| fe-other-P2-06 | ✅ | playground 搜索前缀图标改 `useRenderIcon`（不再传裸字符串） |
| fe-other-E-02 | ✅ | welcome 操作日志时间线补 ReEmpty 空态 |
| fe-A-E01 | ✅ | 移动端也同步 viewportSize（布局自动折叠仍跳过） |
| fe-C-E01/E02 | ✅ | components README §七登记 ReSliderCaptcha / ReTableAction / MessageThreadPanel 关系 |
| fe-D-E02 | ✅ | development-guide 登记 fetch 逃逸白名单（启动探测 + SSE） |
| fe-E-02 | ✅ | 核验 9 处内联 `beforeSure` 均已具备 `closeLoading`/`catch` 或已迁移 `useDialogForm`；缺陷类关闭 |
| fe-E-04 | ✅ | playground 42 条 description 全部入 i18n（zh/en 对称），不再保留中文硬编码 |
| arch §4 插件渠道 | ✅ | `notification-channels.md` §七登记运行期扩展边界（枚举外 code 不可注册）；既有 fail-fast 守护测试覆盖 |

> 本轮验证：前端 vitest **437 文件 / 2831 例**、typecheck/lint、23 项 `check:*` + 行数/hook 门禁全绿；E2E codegen/system-pages/chat/dashboard/ai 双浏览器 **39 + 38 通过**（WebKit chat 上传首轮 flaky、隔离复跑 5/5 通过，按负载瞬态处理）。
> 仍维持触发制/视觉待核：fe-other-E-01 设计器实时预览（增强项）、fe-sys-E-03 / E-05（C 级视觉）、fe-F-03（a11y 滚动扩面）。
