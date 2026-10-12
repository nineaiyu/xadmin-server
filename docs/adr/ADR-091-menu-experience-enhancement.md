# ADR-091：菜单体验增强（高亮路径 / 徽标 / 查询参数 / 分组 / 交互反馈与滚动定位）

- 日期：2026-10-12
- 状态：**已交付**（2026-10-12：后端字段与契约、前端渲染与交互、E2E 用例同批落地）

- 背景：

侧栏菜单此前只承载「标题 / 图标 / 可见性 / 缓存 / 标签页」等基础语义，以下场景缺能力或体验粗糙：

1. **菜单外页面无高亮**：详情页、参数化页面等不在菜单树中的路由（`showLink: false`）进入后，
   侧栏没有任何高亮项；前端 `NavVertical/NavMix/NavHorizontal` **已经有** `meta.activePath`
   消费端，但后端 `MenuMeta` 没有对应字段、序列化器也不下发——链路断在数据侧，功能恒不可用。
2. **无徽标能力**：菜单项无法显示「数字 / 文本徽标」或「红点」，未读 / 待办类信息只能进页面看。
3. **无查询参数能力**：菜单项点击只能裸跳路由，无法携带 `?k=v` 的预置筛选态。
4. **无分组能力**：同级菜单只能平铺，不能按业务语义聚合出分组标题。
5. **交互反馈弱**：菜单项 hover 只变文字色（背景被强制透明），图标无反馈；顶栏弹层依赖
   EP 默认延迟（需要显式固化，避免依赖第三方默认值漂移）。
6. **长菜单无滚动定位**：激活项在滚动区之外时不会自动滚入；侧栏组件职责过载
   （模式分支 + 悬停 + 拖拽 + 滚动计算挤在一个文件里）。
7. **尺寸硬编码**：项高 / 缩进 / 图标间距散落在覆盖样式中，无令牌层。

## 决策

### D1 菜单 meta 扩展六个字段（后端为唯一数据源）

`system.MenuMeta` 新增：

| 字段 | 形态 | 语义 |
|---|---|---|
| `active_path` | `CharField(255, null)` | 菜单外页面（详情页等）进入时高亮的菜单路径；由该隐藏菜单自身配置 |
| `badge` | `CharField(32, null)` | 徽标内容；纯数字大于 99 时前端截断为 `99+` |
| `badge_type` | `CharField`，choices `normal` / `dot`，默认 `normal` | `normal` 显示徽标文本；`dot` 只显示红点 |
| `query` | `CharField(255, null)` | 点击菜单时携带的查询参数，**存储为 `k=v&k2=v2`**；序列化时解析为对象下发（空值下发 `null`） |
| `group_name` | `CharField(64, null)` | 分组标题：同级菜单共享同名时分到同一组 |
| `group_rank` | `IntegerField(null)` | 组间排序（同层升序）；缺省取组内首成员 rank |

- `RouteMetaSerializer` 下发 `activePath / badge / badgeType / query / groupName / groupRank`
  （camelCase proxy，`query` 用 `parse_qsl` 解析为对象）；
- 契约同步：`docs/schema/routes-payload.schema.json` 的 meta 属性补六个键（`additionalProperties`
  维持开放），前端 `pnpm sync:contract` 重生成类型；
- 管理接口（`MenuMetaSerializer`）是 exclude 白名单，新字段自动透出；choices 字段按项目口径
  以 `{value,label}` 下发，前端归一化取标量；
- **`format_menu_meta` 白名单不变**：它只作用于「顶层叶子节点的空目录包装层」，真实节点与其
  子节点 meta 完整透传，新字段不需要进该白名单。

### D2 分组是渲染层装配，不写回菜单树

- 新增纯函数 `groupMenuChildren`（`src/utils/menuGroups.ts`）：把同级 children 编排为
  「普通节点 + 分组虚拟节点」的混合序列——同组节点聚合到首次出现位置，组间按
  `groupRank` 稳定升序，未分组节点保持原相对顺序；
- 虚拟分组节点带 `meta.group = true`，由 `SidebarItem` 渲染为 `el-menu-item-group`
  （折叠态隐藏组标题）；装配只在**渲染处**调用（SidebarItem 的 children、主列/顶栏的顶层
  渲染序列），**不写回 permission store 的菜单树**——激活路径推导、面包屑、单子级折叠、
  一级窄列既有逻辑零影响；
- 组节点只有 `path`（`__menu-group__:<name>` 前缀，仅作 v-for key）与 `meta.title`，
  不参与路由与激活匹配。

### D3 徽标 / 查询参数 / 高亮路径的渲染口径

- 徽标：`SidebarBadge` 组件（根元素用 `i`，避免与「折叠态标题不渲染」的既有 span 判定冲突）；
  展开态内联在标题后、折叠态叠为图标右上角角标（样式层按 `.el-menu--collapse` 定位）；
- 查询参数：`SidebarLinkItem` 在 `meta.query` 非空时以 `{ path, query }` 跳转；外链分支不变；
- 高亮路径：消费端既有（`defaultActive = route.meta.activePath ?? route.path`），本 ADR 补齐
  数据链；示范配置两处——生产种子给「个人中心」菜单（菜单外页面）配 `active_path=/welcome`，
  E2E 种子另有「隐藏详情页 → 高亮列表菜单」的完整用例。

### D4 交互反馈与弹层延迟

- 菜单项 hover：加**主题令牌浅背景**（`--pure-theme-menu-hover`，深浅主题各自取色），
  取代此前的强制透明；图标 hover `scale(1.1)`；图标-文字间距 5px → 8px；
- 弹层延迟：`el-menu` 显式声明 `show-timeout / hide-timeout = 300`（EP 现行默认即 300ms，
  显式固化避免依赖第三方默认值）；
- 子菜单展开动画：移除显式 `:collapse-transition="false"`，恢复 EP 默认过渡（CLS 由 perf 门禁守护）。

### D5 激活项滚动定位

- 纯函数 `computeMenuScrollTop`（`src/utils/menuScroll.ts`）计算目标 scrollTop：
  激活项完整可见返回 `null`（不产生无谓滚动），向上越界对齐容器顶、向下越界对齐容器底，并钳制到内容范围；
- `useMenuScrollActive`（`src/layout/hooks/`）接线：路由/菜单数据变化与子菜单展开事件触发，
  **有界重试（5 次 × 200ms）逐次校正**——EP 自动展开发生在挂载之后且有过渡动画，
  单次测量必然踩在中间态；
- **定位目标选择器必须「叶子优先」**：`.el-menu-item.is-active` 命中失败才退回最深的
  `.el-sub-menu.is-active > .el-sub-menu__title`；把两类选择器合并交给 `querySelector`
  会按文档顺序先命中激活目录标题，退化成「只把目录滚入视口」。

### D6 尺寸令牌化与组件拆分

- 新增导航尺度令牌（`src/style/tokens/primitives.scss`）：`--nav-item-height(50px)`、
  `--nav-item-indent(20px)`、`--nav-item-icon-size(18px)`、`--nav-item-icon-gap(8px)`；
  既有 `--nav-item-radius / --nav-item-inset(-mix)` 不变；覆盖样式改为消费令牌（默认观感不变）；
  层级缩进经 `--el-menu-level-padding: var(--nav-item-indent)` 落到 EP 变量；
- `NavVertical` 拆分（465 → 427 行）：菜单数据装配（整树 / 二级列 / 一级高亮与点击）抽为
  `useSidebarMenu`，滚动定位抽为 `useMenuScrollActive`；模式判定、悬停、拖拽复用既有 hook。

## 影响面

- **契约**：`routes-payload.schema.json` + 前端生成类型；`meta` 为开放对象，向后兼容（旧前端忽略新键）。
- **种子**：`loadjson/menumeta.json` 仅个人中心一条补 `active_path`（其余取默认）；
  E2E 种子新增「E2E菜单演示」场景（徽标 / 红点 / 分组 / 查询参数 / 隐藏详情页）。
- **表单**：菜单管理抽屉的「侧栏与标签」「外链与高级」两组补字段输入（含格式校验：
  高亮路径须 `/` 开头、查询参数须 `k=v(&k2=v2)` 形态）。
- **门禁**：E2E 新增 5 条用例（徽标与分组渲染 / 查询参数跳转 / activePath 高亮 /
  滚动定位 / hover 反馈 / 折叠态角标）；unit 新增 `menuGroups`、`menuScroll`、`SidebarBadge`
  与归一化用例；视觉基线因新增演示菜单重录一次。

## 边界与取舍

- 不替换 Element Plus 菜单组件（自研数据驱动菜单的收益在现需求下不成立，触发条件另行登记）；
- 保留既有本地优势不回退：折叠态悬停展开整栏、移动端 transform 抽屉 + `inert`、
  侧栏拖拽调宽、单子级折叠、激活块「实心主色 + 白字」基线；
- 排序（rank）与权限过滤语义不变；分组虚拟节点不进入路由快照，不影响 `rank` 排序结果；
- `query` 只对「页面菜单」生效（目录/权限点在保存时清空），且字符串形态不做取值类型推断
  （一律按字符串下发）。
