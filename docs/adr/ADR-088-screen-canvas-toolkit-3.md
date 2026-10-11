# ADR-088：大屏画布工具集三期（团队模板 + JSON 导入 + 旋转/动画/富文本 + 网格吸附 + 键盘档位）

- 日期：2026-10-11
- 状态：**已交付**（2026-10-11 立项并交付；一期见 ADR-086，二期见 ADR-087）
- 背景：

一 / 二期交付后，ADR-087 里「登记为后续、触发即做」的条目被一次性拉到台面（模板导入 JSON、模板服务端共享、旋转/动画/富文本、元素级吸附到网格、zoom 键盘档位循环）。它们分两类：

1. **模板能力闭环**：模板目前只有「内置 3 个 + 浏览器本地」两层——换个浏览器就没了、团队之间无法复用；导出 JSON 只能人工看，没有回程入口；
2. **表达与手感**：窗格只能轴对齐、无入场动效、文本只有一段纯文本；自由画布只能靠参考线吸附（画不出「整齐的栅格留白」）；缩放只能点下拉 / 滚轮，键盘用户没有档位入口。

现状约束：

- 服务端是唯一写权威：新增窗格字段必须进 `_normalize_pane_body` 白名单（否则被静默丢弃），新增模型必须带可见性 + 行级归属守卫（非创建者改删 → 1003）；
- 既有载荷契约不回退：新字段一律「**非默认才下发**」，旧载荷字节级不变；
- 既有 E2E（`screen-designer` / `screen-free-canvas` / `screen-free-canvas-2`）是回归基线，不得因默认值变化而失效；
- 前端 bundle 有预算门禁（代码账本 +15 KB 余量），不引入富文本渲染库。

## 决策

### D1 模板服务端共享（团队模板）

- 新模型 `ScreenTemplate`（**独立表**，不复用 `Screen`）：`name`（唯一）/ `design_w` / `design_h` / `panes` / `visibility`，基类与 `Screen` 同款（`DbAuditModel + DbUuidModel` → creator / modifier / 时间戳自动带），`ordering = ("-created_time",)`；
- 载荷口径与自由画布**同形**（`{design_w, design_h, panes}`），复用 `normalize_screen_free_layout` 做归一化与校验：引用可见性 / 尺寸下限 / 画幅范围 / 数量上限全部与保存大屏同口径（引用不可见 = 拒绝，前端应用时按同一口径过滤）；
- 权限复用 `BaseAnalysisViewSet`（`personal` 仅创建者、`shared` 全员、超管绕过；`row_owner_guard` → 非创建者改删 1003，下发 `is_owner`）；路由 `router.register("screen-templates", ...)` → `/api/dataset/screen-templates`；
- 前端模板菜单分三组：**内置**（常量）/ **我的模板**（localStorage）/ **团队模板**（服务端）；操作：「应用」（替换 / 追加）、「另存为团队模板」、「重命名 / 删除」（仅 `is_owner`）、「导出 JSON」；
- 应用模板与粘贴继续共用同一条插入链路（过滤不可见引用 → 新 pk → 偏移 → 夹回 → 上限截断），团队模板不新增第二条实现。

### D2 模板导入 JSON

- 工具条「导入 JSON」→ 文件选择 → `parseTemplateJson` 校验（形状 / 数值 / 长度上限 / 坐标夹取）→ 非法给可读提示且**不写任何数据**；合法进入 D1 同一条插入链路（替换需二次确认，追加直接落）；
- 不信任外部文件：解析即归一化（丢未声明键、夹回画幅、去重 pk），再走插入链路；引用不可见的窗格按既有口径过滤并提示数量。

### D3 旋转

- 窗格新字段 `rotate`：整数度，范围 **-180~180**，`0` 为默认且不下发；
- 语义：**视觉旋转，不进几何**——吸附 / 框选 / 对齐 / 分布仍按轴对齐盒模型计算（旋转后的可视包围盒不参与命中判定），口径唯一：坐标即「未旋转的布局盒」；
- 渲染：`transform: rotate()` 作用在**窗格内容层**（`ScreenPane` 根），不作用在 `ReResize` 盒模型上——手柄不跟着转、拖拽方向仍是屏幕轴；
- 栅格与自由两种模式都支持（旋转溢出属既有行为，画布不裁剪）。

### D4 动画（入场动画）

- 窗格新字段 `animation`：`none` / `fade` / `slide-up` / `slide-down` / `slide-left` / `slide-right` / `zoom-in`，缺省 `none`（不下发）；
- 只影响**展示**：设计器编辑态不播（避免干扰编辑），预览态与投屏页在窗格挂载时播一次；数据刷新不重播（组件不重建）；
- 实现：`paneAnimation.ts` 提供 `paneAnimationClass(name)`；关键帧由展示侧样式定义（`animation-fill-mode: both`、320ms、ease-out），设计器与投屏共用一份类名。

### D5 富文本（受限标记）

- 窗格新字段 `text_format`：`plain`（缺省，不下发）| `rich`；`text` 仍是唯一文本源，长度上限沿用（2000）；
- 标记子集：`**粗**`、`*斜*`、`__下划线__`、`~~删除线~~`、`[c=#rrggbb]彩字[/c]`、换行 → `<br>`；非法颜色按普通文本处理（不报错）；
- 渲染 `richText.ts`（纯函数 + 单测）：**先转义 HTML 再套白名单**，输出只含 `<strong>/<em>/<u>/<s>/<span style="color">` + `<br>`；`v-html` 只接受本函数输出，任何外部 HTML 不直通（XSS 面为 0）；
- 属性面板给「纯文本 / 富文本」切换 + 语法提示；设计器与投屏同渲染，不引入 Markdown 库（bundle 预算）。

### D6 元素级吸附到网格

- 自由模式新增「吸附网格」开关（**默认关**，保持一期已交付手感）+ 步长选择（2 / 4 / 8 / 10 / 16 / 20 px，默认 8），开关与步长记忆在 localStorage（与我的模板同命名空间）；
- 优先级：**参考线优先、网格兜底**——单轴先用既有阈值（4px）吸附其他窗格 / 画幅边界与中线，未命中则四舍五入到步长倍数；两轴独立判定；
- 键盘微调不受网格影响（方向键仍是精确 px；吸附只作用于指针手势）；
- 纯函数扩展 `snapFreeBox(..., grid?)`：`grid` 缺省 = 行为与一期完全一致。

### D7 zoom 键盘档位循环

- `⌘/Ctrl + =`（或 `+`）下一档、`⌘/Ctrl + -` 上一档（在既有档位表内移动，到边界即停）、`⌘/Ctrl + 0` 适应窗口；
- 只在自由模式且非预览 / 只读态生效；输入控件聚焦时不劫持（既有快捷键统一口径）；
- 档位推进**复用二期已有的纯函数** `viewport.stepZoom`（原本服务滚轮，语义完全一致），
  由 `useCanvasZoom` 暴露 `zoomIn / zoomOut`——下拉 / 滚轮 / 键盘三处入口一个真相，
  不为键盘另造一份档位算法。

## 边界（不做）

- 旋转后的几何命中（框选 / 吸附 / 对齐按轴对齐盒，可视溢出不计入）；
- ➡️ 逐元素自定义关键帧（**动画时长 / 延迟 / 循环已由 [ADR-089](ADR-089-screen-canvas-boundary-followups.md) 收回**）；
- 富文本的链接 / 图片 / 表格 / 列表、所见即所得工具条（标记子集已覆盖标题与强调）；
- 模板版本管理、目录分组、审核流、跨租户共享；
- ➡️ 网格吸附的「多元素对齐网格」批量操作（**已由 [ADR-089](ADR-089-screen-canvas-boundary-followups.md) 收回**：坐标 + 尺寸取整到步长，一次一个撤销点）。

> 上表未标 ➡️ 的条目与「跨设备剪贴板」等其余等待条件项，统一登记在
> [ADR-089](ADR-089-screen-canvas-boundary-followups.md) §D3 触发登记表（含触发条件与第一动作）。

## 补充交付（2026-10-11 同日）

ADR-087 里最后一条遗留（**多选批量改属性**）与一条登记为「可选优化」的条目（**手柄命中区随
zoom 缩放**）在同日补齐，边界清单里的「旋转后几何命中」等仍维持不做：

- **多选批量改属性**：采**统一值语义**——多选面板的批量控件显示选区**公共值**（多值不一时留空），
  设定即把同一个值写给所有「该字段适用」的窗格，一次操作一个撤销点；适用性过滤与服务端
  `_normalize_pane_body` 同口径（`size/align/text_format` → 文本类、`fit` → 图片、`rotate/animation`
  → 全部类型），不适用的窗格自动跳过并在面板文案里说明。备选「同时改全部（各改各的）」被否：
  无从表达差异，且撤销会碎成 N 步；
- **手柄屏幕尺寸恒定**：`ReResize` 按 `1/scale` 反缩放八个缩放手柄（CSS 变量 `--re-resize-inv`，
  `scale = 1` 时逐像素不变），解决「适应窗口」下 8px 手柄缩到约 3px 的命中问题——一期 E2E
  曾因此用「手柄角点 +4px」点到窗格本体而误判。
  （此条同时改写一期 E2E 的排障注释，用例本身仍取手柄中心，两种口径都稳。）

## 备选方案与取舍

| 方案 | 结论 |
|------|------|
| 复用 `Screen` 表 + `is_template` 标记（`dform` 先例） | **拒绝**：模板无 `interval/refresh/dashboards`，复用会让 Screen 的校验、投屏链路、数据推送处处判分支 |
| 富文本存 HTML 直通 `v-html` | **拒绝**：XSS 面 + 不可校验；改为受限标记 + 自建渲染（先转义再套白名单） |
| 富文本引入 Markdown 渲染库 | **拒绝**：bundle 预算与可控性（只需 5 种行内样式） |
| 旋转作为几何参与吸附 / 框选 | **拒绝**：轴对齐算法全量复杂化，且「看起来对齐」与「数学对齐」不能有两个口径 |
| 网格吸附默认开 | **拒绝**：会改变一期已交付手感并使既有 E2E 期望漂移；默认关 + 显式开启 + 记忆选择 |
| zoom 键盘做连续缩放（非档位） | **拒绝**：档位是既有唯一口径（下拉 / 滚轮 / 键盘共用），连续值会让下拉显示与记忆值失去意义 |
| 团队模板走「分享链接」而非落库 | **拒绝**：链接无法治理（改删 / 权限 / 可见性），与站点既有 personal/shared 口径也不一致 |

## 兼容性与回退

- 窗格新字段全部「非默认才下发」：`rotate=0` / `animation=none` / `text_format=plain` 时载荷与旧版一致，服务端仍接受缺省（归一化补默认）；
- `ScreenTemplate` 是新增表与新增路由，隐藏入口即可回退；
- 网格吸附默认关、键盘档位是新增按键；富文本默认纯文本；
- 服务端归一化是**白名单**：老版本前端即使发了新字段也会被丢弃（向前兼容），新前端对老数据的缺省渲染与原样一致。

## 验证（实现时）

- 后端：`tests/unit/dataset/test_screen_template.py`（序列化器归一化 / 引用校验 / 画幅与数量上限）、`tests/integration/dataset/test_screen_template_api.py`（CRUD + 可见性 + 非创建者 1003 + 菜单权限）、`tests/unit/dataset/test_screen_layout.py` 扩展新字段白名单与非法值；语言包门禁（新增 msgid 必须进 zh）；
- 前端纯函数单测：`richText.spec.ts`（标记渲染 / 转义 / 非法颜色 / XSS 载荷 / 跨行标记）、`paneEffects.spec.ts`（旋转夹取 / 动画类名与选项映射）、`freeSnap.spec.ts` 扩展（参考线优先 / 网格兜底 / 关闭时行为不变 / 缩放不压破最小尺寸）、`templateJson.spec.ts`（形状校验 / 夹取 / 截断 / 非法输入 / 导出导入往返）、`viewport.spec.ts` 已有的档位推进用例覆盖键盘口径；
- E2E（双浏览器）：`e2e/screen-canvas-toolkit.e2e.ts`（富文本 → 旋转 → 动画（含「编辑态不播、预览态才播」）→ 网格吸附 → 键盘档位 → 团队模板另存 / 应用 / 删除 → JSON 导入），一 / 二期 spec 与栅格 `screen-designer` 作为回归；
- 门禁：client typecheck / lint 三件套 / vitest / 行数 / hook 长度 / i18n 对称 / 契约 / bundle；server 单测 + 集成 + ruff + mypy + i18n + **菜单种子覆盖**（`test_permission_seed_coverage.py`）。

## 落地记录（2026-10-11）

三期全部交付。改动跨两仓（后端新增模型与权限点种子，前端新增表达类能力与模板闭环），
两期成果 + 三期改动一并留在工作区未提交。

**后端（新增一张表 + 窗格白名单扩字段 + 权限点种子）**

- `ScreenTemplate`（`dataset/models/dataset.py`）：`name` / `free_layout` / `visibility`，基类与 `Screen` 同款
  （`DbAuditModel + DbUuidModel`），迁移 `0003_screentemplate`；
- 归一化与校验复用 `screen_layout.normalize_screen_free_layout`，外层包一个语义名 `normalize_screen_template`
  （空模板直接拒）；窗格白名单新增 `rotate`（-180~180）/ `animation`（7 值）/ `text_format`（plain|rich），
  一律「非默认不下发」；
- 序列化器 `ScreenTemplateSerializer`（`row_owner_guard` → 非创建者改删 1003、下发 `is_owner`）；
  引用可见性 / 指标卡取值列校验抽成模块级函数（`visible_dashboard_pks` / `layout_references` /
  `check_metric_value_fields`），大屏与模板共用一份实现（`ScreenSerializer` 的同名方法改为薄委托）；
- `ScreenTemplateViewSet`（沿用 `BaseAnalysisViewSet` 的可见性过滤 + 创建者守卫）+ 路由 `screen-templates`；
- 权限点种子：`loadjson/menu.json` 追加 6 个权限点（list / create / retrieve / partialUpdate / destroy /
  batchDestroy，挂在 `DataScreen` 页面菜单下）+ `menumeta.json` 对应 6 条 meta（uuid5 稳定生成）；
  `sync_menu_permissions` 的模型绑定留待运维命令补齐（种子只保证端点覆盖面，与既有 `destroy/batchDestroy` 行同款）；
- 语言包：zh 补齐 7 条新 msgid（en 同步占位）。

**前端（表达能力 + 模板闭环）**

- 纯函数层：`richText.ts`（受限标记 → 白名单 HTML，先转义再套标记，XSS 面为零）、`paneEffects.ts`
  （旋转夹取 + 动画类名与选项映射）、`templateJson.ts`（解析 / 归一化 / 导出，含大小与数量上限）、
  `freeSnap.ts` 扩展网格吸附（`FreeGridSnap`，参考线优先、网格兜底）、`serverTemplates.ts`
  （团队模板 API 薄层：映射 / 拉取 / 另存 / 重命名 / 删除）；
- 组合层：`createFreeTemplates` 增团队模板域与 JSON 导入（与粘贴共用插入链路）、`createFreeEditor` 增
  网格吸附状态（本地记忆）并透出 `zoomIn / zoomOut`；
- 组件层：`ScreenPane` 增视觉旋转（内容层）+ 动画类（仅展示态）+ 富文本渲染（`v-html` 只吃自己生成的
  HTML）；编辑件（抓手 / 手柄 / 操作件）反向旋转保持轴对齐；动画关键帧抽到共享 partial
  `src/style/screen-animations.scss`（`@use` 进组件 scoped 样式，随组件一起编译）；
- `PaneInspector` 增旋转 / 动画 / 文本格式三项；`FreeCanvasToolbar` 增网格吸附开关与步长、团队模板
  与 JSON 导入导出入口；`shortcuts.ts` 增 `⌘/Ctrl + = / - / 0` 三键。

**踩坑与口径**

- 动画关键帧必须用**独立变换属性** `translate` / `scale`：窗格自身可能带 `transform: rotate()`，
  用 `transform` 做动画会把旋转顶掉（动画结束再跳回去）；
- 富文本标记是**自建白名单**而非 HTML 直通：先转义再套标记，颜色只收 3/6 位十六进制，非法写法原样保留
  （可读且无注入面）；
- 网格吸附的优先级写死为**参考线优先**：若先落网格，已经贴好参考线的窗格会被拽走（`bestDelta` 因此改为
  返回「位移 + 是否命中」）；
- 团队模板与本地模板共用同一套 `FreeTemplate` 视图（`source` 区分来源），因此「应用 / 导出」只有一条实现；
- 新增 ViewSet 端点必须同步 `loadjson/menu.json` 权限点，否则 `test_permission_seed_coverage` 直接红灯
  （本次按命令输出的精确缺口清单手工补齐，未跑全量 `--update-seed`，避免重排既有种子文件）；
- **部署侧收尾（需在连得上库的环境执行）**：`python manage.py sync_menu_permissions --dry-run` 先看缺口，
  再 `python manage.py sync_menu_permissions --grant-roles` 落库——`--grant-roles` 会把新权限点自动授予
  「已拥有同模块权限点与父菜单」的角色，这正是「非超管角色看不到团队模板」的解法（管理员/超管天然放行，
  自定义角色还可在角色页单独收回）。本地库里 `--dry-run` 因连不上 PG 未执行，种子侧由
  `test_permission_seed_coverage` + `check-menu-permissions.mjs` 双向对账兜住。
