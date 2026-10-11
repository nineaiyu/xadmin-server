# ADR-086：大屏自由画布模式（自由像素布局 + 等比投屏）

- 日期：2026-10-11
- 状态：**已交付**（2026-10-11 立项并交付一期 D1~D5；落地记录见文末）
- 背景：

大屏当前有两种形态：`Screen.dashboards` 驱动的仪表盘轮播，与 ADR-062 交付的 **12×60 栅格画布**（格步进吸附、重叠拒绝、服务端单一事实源、设计器与投屏共享几何）。

栅格语义适合「规整的信息面板」，但表达不了另一类大屏诉求：**像素级自由排版**——整幅背景图 + 元素叠放、非整格的精确尺寸、px 级对齐与留白（宣传屏 / 汇报屏 / 底图标注类驾驶舱）。

现状约束：

- 栅格画布的交互、契约、投屏一致性与 E2E 已成型，**不能破坏**（存量大屏、旧客户端、既有用例零影响）；
- 自由画布应作为**新增模式**与栅格共存，而不是改造栅格；
- 组件侧已有交互基础：`ReResize`（绝对定位 + 拖拽移动 + 八向手柄缩放 + 父容器约束 + 几何纯函数与单测）。

## 决策

### D1 模式化共存：新增 `layout_mode` + `free_layout`，栅格载荷与轮播回退不动

- `Screen.layout_mode`：CharField，`grid`（缺省）/ `free`，仅决定「画布态用哪份载荷」；
- `Screen.free_layout`：JSONField（默认空），形如 `{"design_w": 1920, "design_h": 1080, "panes": [...]}`；
- 投屏形态判定（唯一）：`layout_mode="free"` 且自由载荷窗格非空 → **自由画布**；否则 `layout` 非空 → **栅格画布**；两者皆空 → **轮播**（与今天一致）；
- **双载荷各自保留**：模式切换是坐标语义的有损变更，双字段保证来回切换不丢另一套布局；
- 存量零迁移：新字段缺省值（`grid` / 空）使行为与现状完全一致；旧客户端忽略新字段、继续按 `layout` 渲染。

### D2 自由坐标契约：基准设计画幅 + px 坐标 + 数组序即层级

- `design_w` / `design_h`：**基准设计画幅**（缺省 1920×1080；宽 320~7680、高 240~4320）——投屏按此等比适配，字号与间距随整体缩放，避免「分区拉伸变形」；
- 窗格类型与类型字段沿用同一套（`dashboard` / `text` / `clock` / `metric` / `image`），仅坐标段改为 **px**：`x/y ≥ 0`、`w/h ≥ 16`、`x + w ≤ design_w`、`y + h ≤ design_h`；
- **允许重叠**：自由画布的核心能力是层叠（背景图 + 元素叠加）；层级 = 数组顺序（后者在上），**不引入显式 `z` 字段**，层级调整 = 数组重排（置顶 / 置底 / 上移 / 下移）；
- 窗格数量上限：自由模式 **60**（元素更碎），栅格模式维持 24；
- 「重叠允许 + 顺序即层级」与栅格模式「重叠一律拒绝」是两种语义的分界，写进契约注释与属性面板提示。

### D3 校验：服务端第二事实源，类型字段抽取复用

- 新增 `dataset/utils/screen_layout.py::normalize_screen_free_layout`（与栅格版并列）：
  - 归一化画幅（缺省值 / 整数 / 范围钳制与拒绝）与窗格列表；
  - **结构字段同口径同常量**：把 `pk` / `type` / `title` / 文本内容与对齐字号 / 指标三件套 / 图片地址与填充的校验抽成共用 `_normalize_pane_body()`，两版只差坐标段与重叠判定，杜绝「同一字段两套规则」；
  - 坐标校验顺序：整数（`bool` 显式拒绝）→ 非负 → 尺寸下限 → 画幅越界；**不做重叠判定**；
- 序列化器：`validate_layout` 之外新增 `validate_free_layout`，并校验「`layout_mode` 与写入载荷的对应关系」；写入路径永远丢弃未声明键；
- 前端镜像同一口径（新增 `views/analysis/screen/utils/freeLayout.ts`：`clampFreeBox` / `normalizeFreePanes` / 画幅常量，纯函数 + 单测），交互层「夹回画幅」而不是发注定被拒的请求。

### D4 设计器：模式切换 + `ReResize` 承接手势 + 撤销并入既有 history

- 入口：设计器内模式选择（栅格 / 自由），切换**只写 `layout_mode`**，另一模式载荷原样保留、切回即可见；列表行「设计」入口与权限点不变（`partialUpdate:DataScreen`）；
- 自由画布：按 `design_w × design_h` 以 100% 渲染（容器内滚动；缩放编辑见 D6 二期），组件库拖放落点 = 指针相对画布的 px（越界夹回），画布排满/越界给出可读提示；
- 窗格交互使用 `ReResize`：`:x/:y/:w/:h`、`minw/minh=16`、`parentLimitation`、八向 `sticks`；设计态整块可拖（`dragHandle` 留空）+ 点击选中，文本编辑走属性面板；拖拽中只更新本地预览，`dragstop` / `resizestop` 落数据；
- **吸附参考线**：拖动 / 缩放时与「其他窗格的边框与中性线 + 画幅中线」在阈值（4px）内吸附，命中时画布绘制引导线（对齐提示 + 精确落点）。实现接缝 = `ReResize` 增加可选 `snap` 钩子（入参候选 box 与手势方向，返回吸附后 box + 命中的参考线集合），**对齐算法在 designer 侧**（`utils/freeSnap.ts::snapFreeBox`，纯函数 + 单测），组件只做手势与几何——保证组件的通用性不被业务吸附规则污染；参考线随 `dragging` / `resizing` 事件实时更新，`dragstop` / `resizestop` 清空；
- **组件增强（随本 ADR 一并交付）**：`ReResize` 补「props ↔ box 同步」（外部变更如属性面板改数值 / 撤销回放时回写，手势进行中忽略同值回显）与上述 `snap` 钩子，并补单测；
- 撤销 / 重做：`usePaneHistory` 泛型化（现绑定栅格窗格类型）后两模式共用；手势在 `dragstop` / `resizestop` 记撤销点（沿用「位移阈值 + coalesceKey」语义），键盘微调（方向键 1px / Shift+方向键 10px）复用 `nudgeCoalesceKey`；
- 属性面板：复用 `PaneInspector`，自由模式坐标段换 px 步进器并补层级操作。

### D5 投屏：等比缩放（contain）+ 逐窗格刷新 / 导出等价

- 自由画布渲染：设计画幅固定尺寸 + `transform: scale(k)`（`k = min(容器宽 / design_w, 容器高 / design_h)`，`ResizeObserver` 变化重算）+ 居中，letterbox 区域用画布底色；
- 窗格渲染继续复用 `ScreenPane`（同一组件、同一深色视觉），不新造投屏专用实现；
- 数据刷新 / 导出（逐窗格图片 ZIP）/ 远程控制 `refresh` 与栅格态等价；画布态本就不渲染轮播控件、`switch/page` 自然失效（ADR-062 D1 语义不变）；
- 缩放只作用于展示，不写回数据。

### D6 分期

- **一期（本 ADR 交付范围）**：D1~D5 全部实现（含吸附参考线、键盘微调、`ReResize` 两项组件增强）+ 测试与 E2E（自由模式主链路：模式切换 → 拖放 → 八向缩放 → 吸附参考线 → 层叠 → 保存读接口校验 → 投屏缩放断言）+ 文档（`docs/README.md`、组件 README、e2e 台账）；
- **二期（登记，不在本 ADR 交付）**——**已全部交付**（见 [ADR-087](ADR-087-screen-free-canvas-2.md) 与 [ADR-088](ADR-088-screen-canvas-toolkit-3.md)）：
  - 设计器 **zoom-to-fit**：需要给 `ReResize` / 几何补 scale 因子（缩放后指针增量与本地 px 的换算），涉及组件契约，二次立项 → 由 ADR-087 以 `scale` prop 交付；
  - 多选与对齐工具、复制粘贴、自由模式模板 → 由 ADR-087 交付；模板服务端共享、模板导入 JSON、旋转 / 动画 / 富文本、元素级吸附到网格、zoom 键盘档位 → 由 ADR-088 交付。

## 备选方案与取舍

| 方案 | 结论 |
|------|------|
| A. 改造现有栅格画布为「可自由」 | **拒绝**：破坏存量契约（服务端重叠拒绝、E2E、投屏一致性）与栅格体验，两套语义混在一个模型里 |
| B. 单 `layout` 字段 + 模式解释坐标 | **拒绝**：坐标语义随模式变化，切换有损且校验与排障困难 |
| C. 百分比归一化坐标（随容器拉伸自适应） | **拒绝**：大屏需要固定画幅，拉伸变形且字号不随动 |
| D. 双载荷 + `layout_mode`（**采纳**） | 存量零影响、模式可来回切换、校验分支独立 |
| E. 不做（维持栅格 + 轮播） | **拒绝**：自由排版是明确诉求，且组件侧已有交互基础 |

## 兼容性与回退

- 存量数据：新字段缺省值保证行为不变（守护测试锁定默认值与投屏判定顺序）；
- 旧客户端：忽略新字段，继续按现有逻辑渲染；
- 回退：前端隐藏模式入口即可（服务端字段保留，数据库无需回滚）。

## 验证（实现时）

- 后端：`tests/unit/dataset/test_screen_layout.py` 增自由载荷用例（画幅缺省 / 越界 / 尺寸下限 / `bool` 拒绝 / **重叠允许** / 数量上限 / 未知仪表盘与数据集 / 未声明键丢弃 / 模式与载荷一致性）+ `tests/integration/dataset/test_screen_data.py` 增写入回读与非法载荷 400；
- 前端：`utils/__tests__/freeLayout.spec.ts`（画幅夹取 / 归一化 / 落点换算）+ `utils/__tests__/freeSnap.spec.ts`（吸附命中与阈值回退）+ `ReResize` props 同步与 `snap` 钩子用例 + `history` 泛型化回归 + `control` 层回归；
- E2E（双浏览器）：`e2e/screen-designer.e2e.ts` 扩自由模式主链路（或按体量新开 spec），断言拖放后坐标落库、八向缩放生效、**吸附参考线命中后的对齐坐标**、层叠顺序、投屏按容器等比缩放（`transform` 因子与 letterbox）；
- 门禁：server pytest 全量 + ruff + 迁移一致性（makemigrations 无漂移）；client typecheck / lint 三件套 / vitest / 行数 / `check:bundle-size`；视觉基线默认档不改（自由模式仅在显式建屏后出现）。

## 边界

- 不做（**均已由后续 ADR 收回**）：元素旋转 / 动画 / 富文本 → ADR-088 已交付；设计器缩放编辑（zoom-to-fit）、多选与对齐工具（二期）→ ADR-087 已交付；
- 不扩窗格类型（`map` / 视频 / 自定义 HTML 等仍按 ADR-062 的扩展口径：服务端白名单 + 前端 `PANE_DEFAULTS` 同步后再议）；
- 两套校验互不影响：栅格「重叠拒绝」与自由「重叠允许」各自独立，**模式切换不做坐标互转**（避免「看似转换成功、实则布局崩坏」的隐式行为）。

## 落地记录（2026-10-11）

一期 D1~D5 全部交付，另含两处「实现期顺带收口」，均留工作区未提交。

**后端**

- `Screen.layout_mode` / `Screen.free_layout` + 迁移 `dataset/0002_screen_free_layout.py`（缺省 `grid` / 空，存量零迁移）；
- `dataset/utils/screen_layout.py`：抽出共用 `_normalize_pane_body()`（两版校验只差坐标段与重叠判定），新增 `normalize_screen_free_layout()` 与 `active_screen_panes()`（投屏形态判定唯一入口，网格 / 自由 / 轮播的顺序在此收敛），画幅与窗格常量随契约集中；
- `dataset/serializers/analysis.py`：`validate_free_layout` + 「`layout_mode` 与写入载荷对应关系」校验（`layout` 非空 + `mode=free` / `free_layout` 非空 + `mode=grid` 双双 400，杜绝静默回退另一形态）；
- 归一化后的两套载荷在列表 / 详情同时下发（前端按模式取用，模式切换不丢另一套）。

**前端**

- 设计器拆成「组装层 + 两块画布板」：`designer.vue`（模式 / 选中 / 撤销栈 / 保存 / 快捷键）＋ `components/GridCanvasBoard.vue`（既有栅格画布原样迁入，`defineExpose({ canvasEl })` 交出画布供像素→格数换算）＋ `components/FreeCanvasBoard.vue`（自由画布）＋ `components/DesignerToolbar.vue`（工具条，含模式切换）；顺带把栅格度量收敛为 `layout.ts::gridMetrics()` 单一来源（拖拽换算与 drop 落点共用），自由画布域的装载 / 手势落数据 / 会话 / 键盘微调收进 `utils/useFreeCanvas.ts`；
- `ReResize` 两项组件增强：`snap` 钩子（候选盒模型与手势方向入参、吸附结果与参考线集合出参，经 `snapping` 事件回传）＋ props ↔ box 同步（手势中忽略同值回显、外部改写仍生效）＋ 激活态可见手柄；
- 设计器自由画布：1:1 设计画幅（容器滚动）、整块可拖 + 八向缩放（`minw/minh=16` + 父容器约束）、`utils/freeSnap.ts` 参考线吸附（阈值 4px；画幅边界 / 中线 + 其他窗格边框与中性线；命中绘制引导线，停手清空）、组件库拖放按指针 px 落点（越界夹回）、层叠顺序 = 数组序、`PaneInspector` 换 px 步进器并补层级操作（置顶 / 上移 / 下移 / 置底）、两模式各持撤销栈（手势位移为 0 不记点）；
- 投屏：新增 `components/ScreenFreeStage.vue`（设计画幅固定尺寸 + `transform: scale(k)` 等比 contain + `ResizeObserver` 重算 + letterbox 底色）＋ `utils/useFreeStage.ts`（形态判定与可见性过滤，与服务端同序），窗格仍复用 `ScreenPane`（`layout="free"` 时铺满宿主、不渲染栅格抓手）；刷新 / 导出 / 远程控制 `refresh` 与栅格态等价。

**测试与门禁**

- 后端：`tests/unit/dataset/test_screen_layout.py`（画幅缺省 / 钳制 / 非整数回落、`bool` 拒绝、尺寸下限、越界拒绝、**重叠允许**、数量上限、未声明键丢弃、活跃载荷判定顺序）+ `tests/unit/dataset/test_screen_serializer_layout.py`（两套载荷的引用校验与模式一致性）+ `tests/integration/dataset/test_screen_layout_api.py` / `test_screen_data.py`（写入回读、非法载荷 400、双载荷并存）；pytest 全量 exit 0 + ruff + 行数门禁；
- 前端：`freeLayout.spec.ts` / `freeSnap.spec.ts` / `freePaneOps.spec.ts` / `useFreeCanvas` 相关 + `layout.spec.ts` 的 `gridMetrics` + `ReResize` props 同步与 `snap` 钩子用例；
- E2E：新增 `e2e/screen-free-canvas.e2e.ts`（切换模式 → 落窗格 → 拖动命中画幅边界参考线 → 八向缩放 → 保存读接口校验 `layout_mode` / 画幅 / px 坐标 → 投屏断言 `scale` 为单位比例矩阵 → 切回栅格再保存，自由载荷原样保留），双浏览器通过；既有 `screen-designer.e2e.ts`（栅格主链路）作为回归。
- 实现期踩坑记录：E2E 断言保存完成不能只看成功提示（轻量流程里上一次保存的 `ElMessage` 仍在屏，会被旧提示打穿并提前读接口）——改为等待该次 `PATCH` 响应 + 未保存标记消失。
