# 2026-10 前端专项审查 · 分册：其他域（14 页）

> 主册：[code-review-frontend-2026-10.md](code-review-frontend-2026-10.md)。证据等级 A/B/C；问题编号 `fe-other-*`。
> 范围：表单采集 3 页 / 聊天 1 页 / 个人 2 页 / 匿名静态 8 页。只读源码审查，未运行构建与浏览器。
> 横切结论（fe-F-01 / fe-C-01 / fe-C-02 / fe-E-04）沿用主册编号，本文只引用不展开。

## 0. 页面清单

| # | 页面 | 路由 | 入口组件 | 形态 | 复杂度 |
|---|------|------|----------|------|--------|
| 1 | 表单设计 | 动态菜单（locale `dform`） | `views/form/designer/index.vue` | RePlusPage 列表 + 设计器弹窗体系 | 复杂 |
| 2 | 我的填报 | 动态菜单 | `views/form/my/index.vue` | 卡片入口 + RePlusPage | 中 |
| 3 | 表单数据 | 动态菜单 | `views/form/data/index.vue` | 选择器卡片 + 动态列 RePlusPage | 中 |
| 4 | 聊天室 | `/chat`（keep-alive，name=Chat） | `views/chat/index.vue` | 自组装 IM 两栏 | 复杂 |
| 5 | 个人中心 | `/user/info/index`（菜单）+ `/account-settings`（`router/modules/remaining.ts:93`） | `views/account/index.vue` | 分栏 + 10 页签 | 复杂 |
| 6 | 我的消息 | 动态菜单 | `views/user/notice/index.vue` | RePlusPage 薄壳 | 薄 |
| 7 | 登录 | `/login`（public + guest-only，`router/publicRoutes.ts`） | `views/login/index.vue` | 多形态认证外壳 | 复杂 |
| 8 | OAuth 回调 | `/oauth/callback`（public） | `views/oauth/callback.vue` | 状态机落地页 | 中 |
| 9 | OAuth 授权 | `/oauth/authorize` | `views/oauth/authorize.vue` | 同意卡 | 中 |
| 10 | 邀请激活 | `/invite/accept`（public + guest-only） | `views/invite/accept.vue` | 状态机卡 | 中 |
| 11 | 关于 | 动态菜单 | `views/about/index.vue` | 静态信息页 | 薄 |
| 12 | 首页仪表盘 | `/welcome` | `views/welcome/index.vue` | 图表卡片网格 | 中 |
| 13 | 错误页 | `/error/403|404|500`（public）+ `module-disabled` | `views/error/*.vue` | 共享骨架 `ErrorPage.vue` | 薄 |
| 14 | 组件试验场 | `/playground`（`router/modules/playground.ts`） | `views/playground/index.vue` | 索引 + 3 个 demo 页 | 薄 |

---

## 1. 逐页审查

### 1.1 表单设计 form/designer

**标识**：动态菜单路由 → `views/form/designer/index.vue`（32 行薄壳）+ `utils/hook.tsx` 装配；列表元数据驱动（RePlusPage，`auth.create/update/partialUpdate=false` 后由自定义按钮接管）。设计器本体 = 弹窗体系：`DynamicFormForm.vue`（280 行）承载表单定义 + 字段表 + 联动区；字段表 `FormFieldTable.vue` 行内编辑 + sortablejs 拖拽（`useSortable`，`onEnd` 先撤销 DOM 位移再回写数据，避免 el-table 虚拟 DOM 失步）；复杂选项走 `FormFieldOptionsDialog.vue`（结构化行编辑，P2-159 替代逗号/竖线文本协议）；字段完整属性走 `FormFieldDialog.vue`（深拷贝编辑，`getField()` 校验回写）；联动规则 `FormLinkageSection.vue` + `FormLinkageDialog.vue`（多目标展开、条件控件按触发字段类型联动）；版本历史 `SchemaHistoryDialog.vue`；模板复用 `TemplatePickerDialog.vue` + `designerTemplate.ts`（ElMessageBox.prompt 命名）。无独立路由画布——「画布」形态是弹窗内字段表格，无组件面板/快捷键（检索 `keydown|hotkey|ctrl|metaKey` 于 `views/form/designer/` 零命中）。

**UI 实现**：保存前校验链完整（名称/字段必填 → 行内 key/label 同源校验 `fieldValidate.ts` → 子表列必填 → 公式环检测 `assertFormulaAcyclic` → 失效联动剔除并 toast 计数）；行内非法态即时标红（`dform-cell-invalid` 用 `--el-color-danger` 描边，与 el-form 错误描边同色，FormFieldTable.vue:240-245）；编辑前经 `fetchFormDetail` 取 schema 全文，取不到即中止（designerFormDialog.ts:50-55，防半截 schema 覆盖线上定义）；脏检查 `isDirty`（JSON 快照比对）+ `useDesignerGuard` 关闭确认；审批流下拉失败给警告 toast + 行内错误文案（DynamicFormForm.vue:84-94/236-238）。版本历史加载失败保留手写兜底并说明原因（SchemaHistoryDialog.vue:53-61）。

**一致性与体验**：message()/useConfirm/addDialog/ReEmpty/normalizeError 使用符合正面基准（不展开）。fe-C-01 命中：`FormFieldOptionsDialog.vue:153`（el-dialog 直挂）。行内 5 按钮全部展开（showNumber:5/width:320，designerButtons.ts:60-63）。

**问题清单**：
- **fe-other-P2-04（A，低危）字段行删除无确认**：`DynamicFormForm.vue:111-113` `removeField` 直接 `splice`。误删被公式/联动引用的字段时，保存期虽有兜底（公式环报错、失效联动剔除并提示 `dform.linkageDropped`），但联动剔除只给计数 toast、不回滚；兜底是弹窗脏检查（关闭不保存可整体放弃）。建议：删除前 useConfirm 二次确认，或至少在删除被引用字段时提示影响面。
- **fe-other-E-01（E）无 schema 实时预览**：设计器内看不到渲染效果，需保存后到「我的填报」验证（检索 `preview|预览` 于 `views/form/designer/`，仅命中版本历史的只读预览注释）。价值：设计者即时校验联动/选项效果，减少保存-试填往返；成本：低档做法可复用 `form/my/components/SubmissionForm.vue`（接收 `FillableFormItem`，含联动求值）在弹窗内做只读预览 Tab；依赖：SubmissionForm 需允许无 pk 的伪 form 对象（现有 `props.form.pk` 仅在提交载荷用到，预览不提交即可）。

**薄壳外组件备注**：`designerColumns.tsx` 语义 tag 覆写 boolean 列（success/warning/info 语义色），详情抽屉剔除 schema 列避免空行——口径正确。

### 1.2 我的填报 form/my

**标识**：`views/form/my/index.vue` + `utils/hook.tsx` 装配；顶部「可填表单」卡片区（`FormSelectorCard.vue` 收敛三态：失败重试/空态/就绪）+ RePlusPage 列表（列/搜索/行操作经 `useFormMyColumns`/`formMyRowButtons` 装配）。

**要点**：填报弹窗 `formMyFormDialog.ts`（草稿按钮仅草稿态显示；提交前 `validateRequired()` 客户端必填预检，缺失字段行内 `:error` 定位 + watch 补值即清，SubmissionForm.vue:211-241）；动态字段渲染 `SubmissionFieldControl.vue` 三族分发，未知控件类型显式红字提示而非静默空白（:85-87）；编辑停用表单的历史草稿按提交快照渲染（useFormMyActions.ts:36-52）；行按钮按状态机显隐（DRAFT/REJECTED 可编辑删除、PENDING 可撤回、终态只读，formMyRowButtons.ts:17-18 与后端 `_creator_guard` 同口径）；字典拉取失败占位「字典为空」口径、选人远程搜索 300ms 防抖且空关键字清空候选（防无边界枚举）。
**横切**：RePlusPage 列表无内联 error 态 → **fe-F-01**（引用）；顶部卡片区有独立失败态（`formsLoadFailed` + retry）——列表页与入口区反馈不对称属框架已知项。
**必查项**：多形态/极端数据见 fe-other-P2-05；暗色红旗无命中。

**问题清单**：
- **fe-other-P2-05（A）提交数据摘要列无长度收敛**：`submissionData.ts:2-11` 对非标量值直接 `JSON.stringify`，`useFormMyColumns.ts:77-90` 注入列渲染裸 span（无 truncate/tooltip/字数上限）。口径：明细子表行、附件数组、daterange 等对象字段全量 JSON 化后进单元格；一行填报含大子表时单元格文本可达数千字符（无上限），el-table `.cell` 默认换行使行高被撑爆。对照：管理端 form/data 的动态列走 `fieldValueText`，table 类型收敛为「N 行」（format.ts:61-65）——同仓库两套口径不一致。建议：摘要列复用 `fieldValueText` 或加长度截断 + tooltip。

### 1.3 表单数据 form/data

**标识**：`views/form/data/index.vue`（237 行）+ `utils/hook.tsx` 装配（selection/filters/columns/userLabels/detail/buttons 七个子模块）；顶部选择器卡片（FormSelectorCard 复用，locale `formData`）+ 字段筛选区 + 按 `selectedFormPk` 作 key 重建的 RePlusPage。

**要点**：筛选控件按字段类型分发（date/switch/选项多选/数值/选人远程/级联/文本 300ms 防抖即输即筛）；切表单时清空筛选、重写 `api.form/dataFields/filterData`（hook.tsx:77-82）；动态列紧随 form_name 列注入、列被移除时兜底追加（useFormDataColumns.ts:84-105）；字典/选人回显缓存化；数据范围与字段权限提示用 el-alert 常驻（index.vue:212-223）；管理端只读（create/update/destroy 全关）；详情 `formDataDetail` 管理端端点含掩码口径，与「我的填报」端点区分（SubmissionDetail.vue:41-49 注释）。
**横切**：RePlusPage 列表无内联 error 态 → **fe-F-01**（引用）。
**必查项**：未选表单时 ReEmpty 占位（image-size 80）；筛选值随表单切换清空无残留；暗色红旗无命中。未发现问题。

### 1.4 聊天室 chat

**标识**：`views/chat/index.vue`（209 行装配）+ hooks 域拆分（core/actions/lifecycle + send/attachments/streaming/socket/history/scroll/messages/rooms 十余个单职责模块）+ components（Sidebar/Window/Bubble/Media/ComposerToolbar/EmojiPanel/GroupMembersPanel/GroupCreateForm）。左栏会话+联系人、右栏 `MessageThreadPanel` 共享壳（与 AI 助手页同源）；窄屏（`useFullHeightPanel` isNarrow）左栏折叠为 `ReNavDrawer`。路由/keep-alive 依赖组件名 `Chat`；通知点击带 `?room=` 定位（index.vue:36-41/61）。

**UI 实现**：
- 消息区：时间分组（`useMessageTimeGroups`）、向上分页（`useHistoryPagination` before_id 游标 + 滚动位置补偿）、离底检测 + 新消息悬浮条（`useMessageScroll`，`pendingCount`）、尾部追加进入动画（`useListAppendAnimation` 仅尾部播一次）；乐观上屏 `client_msg_id` 对齐（chatMessages.ts 五处写入口径集中单文件）；撤回窗口 2 分钟与服务端同源、15s 巡检复位（chatMessages.ts:33-40/157-165）。
- 输入区：Enter 发送 / Shift+Enter 换行（keydown.enter.exact.prevent，ChatWindow.vue:367）；表情面板光标处插入 + 焦点恢复（ChatWindow.vue:147-158）；附件入口按 `upload:ChatMessage` 权限收敛，MIME 分流 video/audio/file（ChatComposerToolbar.ts:53-61）；`@` 联想候选 = 会话成员 ∪ 在线联系人按 pk 去重取 6 条（useChatMention.ts:25-37），AI 会话不触发。
- AI 面板：SSE 流式 `AiStreamingBubble`（reasoning/content 双增量、stop 按钮），流归属当前会话才渲染（ChatWindow.vue:207-211）；动作草稿确认卡复用助手页 `MessageActionAttachments`，执行按 `actionExecute:AiAssistant` 权限收敛且 412 approval_required 分流（MessageBubble.vue:112-138）。
- 连接态：头部 tag 三态（denied 红色无权限 / 未连接黄色「连接中」/正常无 tag，ChatWindow.vue:269-274）；WS 上行收口（未连接/异常 → 乐观行标记失败 + 复用断连提示，useChatSend.ts:40-58）；已读上报尽力而为（markRead 静默容错，useChatSocket.ts:47-62）；群管理弹窗 addDialog 统一（ChatWindow.vue:185-204），退群/改名/成员变更经 `roomChanged/left` 同步列表。
- 附件消息：图片缩略+预览、音视频原生播放器、文件卡片下载，取件统一受鉴权端点，失效附件占位文案（MessageBubbleMedia.vue 三态占位一致）。
- 长文本：气泡 `wrap-break-word whitespace-pre-wrap` + `max-w-[72%]`（MessageBubble.vue:166-169 + ChatTextBubble）；未读徽标 99+ 封顶（ChatSidebar.vue:228-232）；会话摘要 `truncate`。

**一致性与体验**：message/useConfirm/useDialogForm/ReEmpty 符合正面基准；弹层统一 addDialog（群成员/群创建）。桌面通知开关含能力检测与拒绝提示（ChatWindow.vue:165-180）。切会话清草稿只按「有效会话」比较避免列表刷新误清（ChatWindow.vue:229-238）——细节到位。

**问题清单**：
- **fe-other-P2-01（A）AI 流式进行中发送消息被静默丢弃**：`ChatWindow.vue:218-224` `submit()` 先清 `draft` 再 emit → `chat/index.vue:110-113` 路由到 `sendAi` → `useChatStreaming.ts:51-53` `if (... || options.streaming.value) return;` 直接返回。流式期间输入下一问并回车（发送按钮也未因 streaming 置灰，仅 `!room || !draft.trim()` 判定）：输入框清空、无气泡、无 toast，消息消失。建议：流式期间禁用输入/发送并提示，或实现排队。
- **fe-other-P2-02（A）附件上传中零反馈**：`useChatAttachments.ts:43-63` 先 await 上传、成功后才乐观上屏；期间仅 `uploading.value=1` 令工具条附件按钮置灰（ChatWindow.vue:356-360），无进度条/无 toast/无占位气泡。大文件（服务端上限内）上传窗口内用户无任何「正在上传」感知，易以为点击无效。建议：乐观占位气泡（文件名 + loading 态）或工具条 loading 指示。
- **fe-other-P2-03（A）@ 联想候选无键盘支持**：候选区仅 `@click="insertMention"`（ChatWindow.vue:336-351），无高亮项/上下键/Enter 选择；输入框 Enter 直接 `submit`（:367）。键入 `@` 后回车会把原始 `@片段` 当消息发出。建议：候选打开时接管 Enter/ArrowKeys（微信式），Esc 关闭。
- 观察（不编号）：`useRooms.ts:39-41` `unreadTotal` 计算后无任何消费方（检索全仓仅定义与返回），属死代码；chatFrames 群变更帧仅带 room_id 即整表刷新会话列表（注释已说明幂等，可接受）。

### 1.5 个人中心 account（含 /account-settings 合并审查）

**标识**：菜单路由 `/user/info/index` 与框架路由 `/account-settings`（remaining.ts:93-102，与动态菜单 UserInfo 共用组件）同挂 `views/account/index.vue`（217 行）。分栏骨架 `ReSplitPane`（宽度持久化，默认 15%）+ `AccountSidebar`（返回/名片/页签导航，`panes.ts` 定义 10 页签、页签级权限门）；菜单外全屏页需自装 tippy（index.vue:22-27 注释）。页签定位支持 `?tab=` 直达（非法/无权限回落 profile 并 toast，index.vue:52-61）；切换页签 `history.replaceState` 同步（注释说明避开 router.replace 触发在途请求中止的竞态）。低频重面板 defineAsyncComponent 拆 chunk（panes.ts:23-40）。

**UI 实现**：`AccountPanel.vue` 统一四件事（容器宽/标题层级/内容纸/行节奏）并收敛 `.account-row` 系列共享样式（非 scoped 挂前缀、注释说明原因）。安全分区拆解：
- **MFA（UserMfaPanel.vue，422 行）**：状态未知时禁用绑定入口 + 重试（:79-97，防「未知落未绑定」误开放）；绑定流程 扫码→输码→确认；恢复码一次性展示弹窗（append-to-body、禁点遮罩关闭、复制按钮）；关闭/解绑/重新生成恢复码走 `handleOperation` 敏感操作全局验证弹窗（el-popconfirm 二次确认前置）。**fe-C-01 命中：UserMfaPanel.vue:389**（el-dialog 直挂，恢复码展示）。
- **Passkey（UserPasskeyPanel.vue）**：WebAuthn 注册仪式；列表/删除走个人白名单路由；删除 useConfirm 确认、异常 normalizeError。**fe-C-02 命中：UserPasskeyPanel.vue:53**（ElMessageBox.prompt 输入凭据名）。
- **第三方账号（OAuthBindings.vue）**：绑定经 IdP 跳转 + sessionStorage 绑定意图标记（TTL 读后即焚）；解绑 `ElMessageBox.prompt` 口令二次确认，口令 AES 加密提交不落明文（:83-110）。**fe-C-02 命中：OAuthBindings.vue:83**。
- **改密（ChangePassword.vue）**：zxcvbn 强度条 5 段 + 密码规则接口预检 + 一致性校验与登录/注册同源（buildRepeatPasswordValidator）；字段口径注释消歧（`sure_password` 即新密码，new_password 仅本地校验不进载荷——useAccountManage.ts:80-93）。
- **偏好（Preferences.vue）**：初态未知禁用开关防反向写入、乐观开关失败/异常回滚（:34-75）——敏感态处理是正面样板。
- **PAT（AccessToken.vue，427 行）**：创建走专用弹层（明文令牌一次性展示弹窗，`dark:bg-gray-800` 暗色适配）；吊销二次确认（危险色）；scopes 条数 + tooltip 明细、IP 白名单行数、调用记录弹窗；框架默认新增/编辑/删除全关。
- 其余：Profile（PlusForm + 头像裁剪上传 + 岗位只读标签）、AccountManagement（改密/绑定邮箱手机入口，绑定冲突强制勾选确认解绑 confirmBindConflict）、SecurityLog/AccessToken 内嵌 RePlusPage（自适应 offsetBottom 150 实测注释）、AuthorizedApps（撤销走共享组件带确认）。

**一致性与体验**：message/useConfirm/handleOperation/addDialog 符合正面基准；敏感操作（改密/解绑/吊销/关闭 MFA/解绑恢复码）全部有二次确认或全局验证弹窗，闭环完整。`!important` 密集命中 `index.vue`（菜单皮肤，:160-216）：颜色全取主题变量、权重兜底有注释论证（含「必须带锚点防外溢全站」的教训记录）——判为有据可依，非红旗。`#[0-9a-f]{6}` 仅 ReQrcode 的 logo 底衬 `#ffffff`（二维码 canvas 功能性白底，与主题无关）——非红旗。

**问题清单**：无新增编号问题。备注（观察）：UserMfaPanel 的 `handleStartBind/handleConfirmBind/handleOpen/handleTest`（:104-115/:117-138/:150-167/:169-184）promise 链缺 `.catch`，HTTP 层异常时依赖拦截器兜底但会产生 unhandled rejection 告警；同文件其余调用均有 catch，属一致性小疵。

### 1.6 我的消息 user/notice

**标识**：`views/user/notice/index.vue`（34 行薄壳）+ utils 装配（columns/dialog/buttons）；RePlusPage 元数据驱动（locale `userNotice`）。

**要点**：标题列字典驱动 notice_level（字典色优先 el-text style、无色回退枚举即类型契约，userNoticeColumns.tsx:14-25）；工具栏「批量已读」（选中数>0 才显示 + 独立确认文案，避免复用删除确认误导）与「全部已读」（unreadCount>0 才显示，useUserNoticeButtons.ts:44-67）；详情弹窗：未读行内就地已读（无 batchRead 权限不自动标记，防点开必 403）、深链 `?pk=` 消费后 `history.replaceState` 剥离（绕过 vue-router 防整页重挂载卸弹窗，userNoticeDialog.ts:17-39 注释）、深链关闭清过滤重取全量。全局未读数回写 notice store（hook.tsx:71-74）。
**横切**：RePlusPage 无内联 error 态 → **fe-F-01**（引用）。
**必查项**：超长标题由表格单元格 + el-text 渲染（wrap 行为随框架列宽），未见红旗；暗色无命中。未发现问题。

### 1.7 登录 login

**标识**：`views/login/index.vue`（223 行外壳）+ 子页 `components/Basic.vue`（账密 322 行）/`Login.vue`（验证码 200 行）/`Register.vue`（176 行）/`ResetPassword.vue`（130 行）/`LoginMfa.vue`（146 行，与 oauth/callback 复用）+ 共享流 `useLoginFlow.ts`/`utils/*`（loginShared/useRememberLogin/useVerifyCodeSubmit/useAuthInfo/useEnterSubmit）。能力清单外壳统一拉取（`loginPageStore.capabilities`），就绪前不渲染子页（防「服务已禁止」闪现）、失败整页重试（index.vue:39-60/162-185）。`publicRoutes.ts`：/login 未登录可达、已登录不停留（GUEST_ONLY）。

**UI 实现**：
- 多形态切换：子页 v-if 互斥挂载（currentPage store 驱动），「其他登录方式」入口按能力清单过滤（visibleOperates，Basic.vue:62-70）；账密被关闭时给可读 el-alert 而非空表单（Basic.vue:250-290）；access 整体关闭时 el-result「服务已禁止」且入口按钮全隐（防死循环点击面，Basic.vue:300-301）。
- 一次性资源管理：临时 token 首取 Promise 复用 + 提交前 await（防空 token 400）、失败/异常后强制刷新 token 与图形验证码（Basic.vue:84-112/136-141 注释）；MFA 返回重新登录同样刷新（onMfaBack）。
- MFA 流转：`mfa_required` → LoginMfa（方式单选 → 挑战码发送（冷却倒计时禁用 + 秒数文案，LoginMfa.vue:87-101）→ 动态码/Passkey 验证）；通过走 `afterTokenIssued` 统一链（写 token → 改密标记 → initRouter → redirect）；失败回退 `handleMfaBack` 刷新资源（useLoginFlow.ts:100-103）。
- 错误回显：handleOperation 默认失败文案 + 业务失败刷新一次性资源；注册显式分流 200 信封业务码（防「先提示成功再跳转」，Register.vue:49-67）。
- 密码强度：注册/重置用 `createPasswordFormRules`（passwordRulesCheck 与后端同源 + 一致性必填）；确认密码空值必填拦截（防空口令到服务端才报错，loginShared.ts:99-113）。
- 隐私弹层：LoginPrivacyNotice（登录即同意 + 条款弹窗 append-to-body，注释记录 Motion transform 裁切 fixed 遮罩的教训）；注册页同款（Register.vue:160-174）→ **fe-C-01 命中：LoginPrivacyNotice.vue:21 / Register.vue:160**。
- 主题/语言：登录页在布局外，自行应用主题 + watch 站点配置同步（未登录首访拿不到服务端 DarkMode 的边界有注释，index.vue:84-98）。
- 倒计时态：发码按钮倒计时禁用 + `text+login.info` 文案，倒计后自动恢复（ReSendVerifyCode 共享组件 :157-168）；图形验证码点击刷新、消费后强制刷新。

**一致性与体验**：message/useEnterSubmit/useVerifyCodeSubmit（ref 空值保护收敛）符合正面基准。**fe-E-04 命中：ReTypeit/src/index.tsx:38**（浏览器语言分支的调试文案，已立项）。

**问题清单**：无新增编号问题。备注（观察，不编号）：①子页切换即重挂载，账密页已输入的用户名/密码不跨形态保留（Register/ResetPassword 返回 Basic 后字段清空）——符合安全直觉，未列问题；②`RememberLogin.vue` 内嵌原生 select（天数）无外观样式依赖、含 aria-label（注释记录 a11y 修复史）；③登录成功 `initRouter` 失败跳静态 `/error/500` 可重试（useLoginFlow.ts:76-78），闭环。

### 1.8 OAuth 回调 oauth/callback

**标识**：`/oauth/callback`（public，publicRoutes.ts:17 注释说明 code/state 即凭据必须 public）；`views/oauth/callback.vue`（200 行状态机）。

**要点**：三意图按载荷字段判定（bound / mfa_required / 令牌）；MFA 分支复用 LoginMfa（原实现丢弃 mfa_token 使 OAuth+MFA 用户死路，注释记录修复史 :30-34）；失败分支出口按钮按 sessionStorage 绑定意图标记（TTL、读到即焚、登录发起时清除防误导 :46-55/85-89）；IdP `error`/`error_description` 外部输入仅纯文本插值（el-result sub-title，无 HTML 注入面 :93-104）；绑定成功 `router.replace` 防回退重放已消费回调（:123-127）；`must_change_password` 与密码登录同口径（防借 OAuth+MFA 绕过改密引导 :66-82）；200+空 data 明确报错不静默白屏（:134-137）；加载失败反馈：error 态 el-result + 出口按钮，loading 态 v-loading 60vh 容器。**未发现问题**；多形态状态残留：意图标记读后即焚，无残留。

### 1.9 OAuth 授权 oauth/authorize

**标识**：`/oauth/authorize`（remaining.ts:81-90，第三方发起独立无侧栏）；`views/oauth/authorize.vue`（246 行）。

**要点**：展示域取服务端校验通过的 `info.redirect_uri` 而非地址栏 query（防伪造域名钓鱼，:27-36 注释）；写方法 scope 升级 warning 提示（riskyScopes 正则 :37-42）；PKCE 必选/可选提示；拒绝也走服务端（redirect_uri?error=access_denied）；回调地址不可解析时明确告知「授权已生效」防重复授权（:94-97）；加载失败/空 data/非法请求三态 el-alert。**未发现问题**；暗色无命中。

### 1.10 邀请激活 invite/accept

**标识**：`/invite/accept`（public + GUEST_ONLY，令牌即凭据）；`views/invite/accept.vue`（247 行五态状态机：loading/pending/accepted/invalid/error）。

**要点**：令牌入页即从地址栏剥离（history.replaceState，:41-53，Referer/历史不落凭据）；网络/5xx 与「链接无效」分离（error 态给重试而非误导失效，:70-75）；密码规则与令牌预检解耦（规则失败仅 warning 不阻塞，后端最终校验 :62-69）；本地预检（必填/一致/规则/验证码）+ AES 加密开关（加密失败绝不降级明文，:117-121）；图形验证码消费后清 code；成功 1.2s 后跳登录。加载失败反馈完备（error 态 + 重载按钮）；状态残留：无（单次状态机）；暗色无命中。**未发现问题**——本页是匿名页正面样板。

### 1.11 关于 about

**标识**：`views/about/index.vue`（263 行）+ `columns.ts`；RePage 布局。

**要点**：信息层级清晰——关于卡（版本 tag + 简介 + 技术栈徽章派生自 package.json）→ 平台信息卡（键值对 + 外链行分隔线）→ 生产/开发依赖卡（主依赖 danger 色高亮、npm 外链、窄屏降列）；`!important` 命中 :243-262 但注释论证为压 EP 0-4-0 权重的必需项（非样式债）；空态不适用（静态页无请求）。**未发现问题**。

### 1.12 首页 welcome

**标识**：`views/welcome/index.vue`（263 行）+ `hook.tsx`/`dashboardState.ts`/`dashboardFetchers.ts` + Chart* 组件；echarts 懒加载就绪后渲染（首屏骨架防塌陷 CLS，index.vue:130-132）。

**要点**：整页失败 el-alert + 重载（:58-69）；数据范围受限 warning 提示；AI 日报卡按权限点拉取、关闭时整卡不渲染；六取数并行、单卡失败只置失败标记不互相阻断（hook.tsx:31-54）；时间线节点色按 EP 语义色循环（替换原随机渐变，注释）；时间戳对比度加深（axe color-contrast 修复注释 :257-261）；首屏不用入场动画（LCP/CLS 注释）。

**问题清单**：
- **fe-other-E-02（E）操作日志时间线无空态**：`index.vue:209-240` 时间线卡片对 `operateLogList` 空数组无占位（空权限/新站点时卡片仅剩标题）。价值：与全站 ReEmpty 口径一致，避免「卡片空白像加载失败」误读；成本：低（ReEmpty size small 一行）；依赖：无。

### 1.13 错误页 error（403/404/500 + module-disabled，按 1 页计）

**标识**：`views/error/{403,404,500,module-disabled}.vue`（各 12-23 行）共享 `components/ErrorPage.vue`（107 行骨架）；路由 `router/modules/error.ts`，403/404/500 在 `PUBLIC_ROUTE_PATHS`（未登录可达）。

**要点**：状态码大标题 + 说明 + 「返回」主按钮（`hasInAppHistory` 区分站内返回/回首页文案）；诊断信息区 `?detail=&requestId=` 纯文本展示 + 一键复制（复制失败 toast，:51-61）；三段渐入动效尊重系统「减少动效」（usePreferredReducedMotion 优先于页面开关，:35-39）；module-disabled 变体关动效 + 模块名标注 + 次级按钮插槽。引导闭环完整。**未发现问题**。

### 1.14 组件试验场 playground

**标识**：`views/playground/index.vue`（144 行索引）+ `registry.ts`（43 组件注册表，vitest 与 components/README.md 双向守护）+ demo 页 display/layout/input；路由 `router/modules/playground.ts`。

**要点**：分组浏览 + 名称/描述过滤（空结果 ReEmpty）；有样例/仅文档双 tag 区分（docs-only 卡片降透明度）。开发工具定位。

**问题清单**：
- **fe-other-P2-06（C）搜索框 prefix-icon 传原始字符串失效**：`playground/index.vue:83` `:prefix-icon="'ep:search'"`。依据：EP 经 `resolveDynamicComponent(prefixIcon)` 渲染（element-plus input.vue_vue_type_script_setup_true_lang.mjs:419），字符串按组件名解析；本仓全局仅注册 IconifyIconOffline/FontIcon 等（main.ts:66-72），无 `ep:*` 图标组件注册源，全仓仅此一处传裸字符串（其余均 `useRenderIcon(...)`）。推断图标不渲染 + 运行时 resolve 告警；未运行实测，故 C 级。修复：改 `useRenderIcon` 导入的 Search 图标。
- **C 级待验证：硬编码中文与 fe-E-04 口径冲突**：`registry.ts` 42 条 `description` 为中文用户可见文本（卡片直出，:34-245），demo 页另有 props 速查中文 code 块与中文示例长文本（layout.vue:135、input.vue:29-76、display.vue:19-20）。fe-E-04 结论「硬编码中文用户可见仅 1 处」与本域扫描结果冲突——需确认 playground 是否在「用户可见」口径外豁免（开发工具页）：若纳入，建议在主册 fe-E-04 追加或在词条化改造时一并处理；若豁免，建议在主册备注豁免范围以免后续复查重复争议。`check-i18n-keys.mjs` 只校验 t() 引用、不扫裸中文，此面无门禁兜底。

---

## 2. 本分册小结

### 2.1 发现统计

| 类别 | 数量 | 编号 |
|------|------|------|
| 引用横切 | 4 类 10 处 | fe-F-01（form/my、form/data、user/notice、account 内嵌列表同此形态）；fe-C-01（FormFieldOptionsDialog:153、UserMfaPanel:389、LoginPrivacyNotice:21、Register:160）；fe-C-02（UserPasskeyPanel:53、OAuthBindings:83）；fe-E-04（ReTypeit index.tsx:38） |
| 新增 P2 | 6 | fe-other-P2-01 ~ 06（chat 3 / designer 1 / form-my 1 / playground 1，其中 P2-06 为 C 级） |
| 新增 E | 2 | fe-other-E-01（designer 预览）、fe-other-E-02（welcome 空态） |
| C 级待验证 | 1 | playground 硬编码中文与 fe-E-04 口径冲突 |
| 无问题页 | 7 | form/data、user/notice、oauth/callback、oauth/authorize、invite/accept、about、error |

问题分布口径：chat 域问题集中在「进行中过程的反馈与键盘交互」（流式/上传/联想三个中间态），设计器与填报表单是「数据破坏面的小兜底缺口」，匿名静态页整体质量最高（状态机 + 失败反馈 + 安全脱敏全部到位）。

### 2.2 移交全栈审查（前端侧证据，需后端/联调确认）

1. **AI 流式并发语义**（fe-other-P2-01）：后端 `stream_ai_message` 是否支持同会话排队或并发拒答？若服务端本就拒绝并发，前端静默丢弃是「衔接缺失」，修法应前后端对齐（排队 vs 显式禁用）。
2. **附件上传进度**（fe-other-P2-02）：上传端点是否可提供进度事件（axios onUploadProgress）或分片，决定是「假进度（不确定态 spinner）」还是真进度。
3. **字段删除影响面**（fe-other-P2-04）：后端对「删除被引用字段」的保存拒绝已有（公式环/未知字段），但「联动静默剔除」目前仅前端计数提示；是否需要后端在 schema diff 响应里回传被剔除规则明细，供 UI 确认框展示。
4. **摘要列收敛口径**（fe-other-P2-05）：`fieldValueText`（管理端动态列）与 `submissionDataText`（个人页摘要）是否统一为一份字段值文本化协议（建议后端 table_fields 直接下发摘要或前端抽公共 util + 单测）。
5. **playground 词条化豁免口径**：i18n 门禁不扫裸中文，若 playground 定位开发工具需在 `check:i18n` 或 review 基线中显式登记豁免清单。

### 2.3 Top 发现

1. **fe-other-P2-01**：AI 流式期间发送消息静默丢失（输入被清、零反馈）——聊天页最易触发的数据丢失面，修复点集中在两个函数。
2. **fe-other-P2-02**：附件上传中零反馈——上传窗口与感知断裂，E2E 与真实大文件场景都会暴露。
3. **fe-other-P2-05**：填报摘要列 JSON 全量入格——极端数据下撑爆行高，且与管理端口径漂移。

### 2.4 已读文件（证据基础）

`views/form/designer/{index.vue,components/{DynamicFormForm,FormFieldTable,FormFieldDialog,FormFieldOptionsDialog,FormLinkageSection,FormLinkageDialog,SchemaHistoryDialog,TemplatePickerDialog}.vue,utils/{hook.tsx,designerColumns.tsx,designerButtons.ts,designerFormDialog.ts,designerTemplate.ts,fieldValidate.ts,schemaMeta.ts}}`；`views/form/{my/{index.vue,utils/{hook.tsx,useFormMyActions.ts,useFormMyColumns.ts,formMyFormDialog.ts,formMySubmissionActions.ts,formMyRowButtons.ts,submissionData.ts},components/{SubmissionForm,SubmissionFieldControl}.vue},data/{index.vue,utils/{hook.tsx,useFormDataColumns.ts,useFormDataFilters.ts,format.ts}},components/{FormSelectorCard,SubmissionDetail}.vue}`；`views/chat/{index.vue,components/{ChatWindow,ChatSidebar,MessageBubble,MessageBubbleMedia,ChatComposerToolbar,ChatEmojiPanel,ChatGroupMembersPanel,ChatGroupCreateForm}.vue,hooks/{useChat,useChatCore,useChatActions,useChatLifecycle,useChatSocket,useChatSend,useChatAttachments,useChatStreaming,useChatScroll,useChatHistory,useChatReactions,useChatMention,useRooms,chatMessages,chatFrames}.ts}`；`views/account/{index.vue,utils/{panes.ts,useAccountManage.ts,useUserProfileForm.ts,useAvatarUpload.ts,confirmBindConflict.ts,hook.tsx},components/{AccountPanel,AccountSidebar,Profile,Preferences,Notifications,AccountManagement,ChangePassword,BindEmailOrPhone,UserMfaPanel,UserPasskeyPanel,OAuthBindings,AuthorizedApps,SecurityLog,AccessToken,ReQrcode/src/index.tsx}.vue}`；`views/user/notice/{index.vue,utils/{hook.tsx,userNoticeColumns.tsx,userNoticeDialog.ts,useUserNoticeButtons.ts}}`；`views/login/{index.vue,useLoginFlow.ts,utils/{loginShared.ts,useAuthInfo.ts,useRememberLogin.ts,useVerifyCodeSubmit.ts},components/{Basic,Login,Register,ResetPassword,LoginMfa,LoginPrivacyNotice,RememberLogin,PasswordFields,OauthEntry}.vue,components/ReTypeit/src/index.tsx}`；`views/oauth/{callback,authorize}.vue`；`views/invite/accept.vue`；`views/about/index.vue`；`views/welcome/{index.vue,hook.tsx,components/AiDailyReportCard.vue}`；`views/error/{403,404,500,module-disabled}.vue` + `components/ErrorPage.vue`；`views/playground/{index.vue,registry.ts,display.vue,layout.vue,input.vue}`；旁证：`router/{publicRoutes.ts,modules/{remaining,error,playground}.ts}`、`components/ChatTextBubble/index.vue`、`components/ReSendVerifyCode/src/index.vue`（倒计时段）、element-plus input 源码（icon 解析路径）、`scripts/check-i18n-keys.mjs`（门禁口径）。扫描命令：`grep -rn "#[0-9a-fA-F]{6}|!important|[一-龥]"` 于 11 个域目录（含 scss/css），结果已逐条甄别（红旗零新增，命中均有注释论证或功能性说明）。

---

*审查日期：2026-10-26 · 审查方式：只读源码（未运行构建/dev server/浏览器）· 证据等级：A=源码直读且行为可静态推断；B=依赖共享组件行为推断；C=需运行时验证。*
