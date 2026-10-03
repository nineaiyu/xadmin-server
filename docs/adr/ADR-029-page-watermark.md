# ADR-029：敏感页面水印（G10，配置化）

- 状态：已接受
- 日期：2026-09-13
- 关联：年度开发计划 2026.10-2027.09 §四 2027-08（G10）；`server/conf.py` 与
  `settings/serializers/basic.py`（基本设置通道）；`@pureadmin/utils` 的 `useWatermark`
- 编号说明：2027-08 窗口的 G9 全局搜索预留 ADR-028（尚未落地），本篇（G10）先落地，
  故编号落在 029；ADR-030 预留给 G11 开放平台雏形

## 背景

现状：`FRONT_END_WEB_WATERMARK_ENABLED`（基本设置）开启后，登录/用户信息接口下发「用户名-昵称」水印，
由 user store 在 `getUserInfo` 时一次性挂载 —— **全站生效、无时间、且与设置面板的本地水印耦合在一处**
（`this.clear = clear` 把 store 状态字段当水印清除函数用的历史 hack）。

G10 要求「敏感页面水印（配置化）」：只有敏感页面（用户、角色、审批单等）需要水印，
且水印要能回答「谁、在什么时间」看了/截了哪个页面 —— 即**范围可配 + 内容含时间**。

## 决策

### 1. 配置项落在「基本设置」（三项，零新增接口/表/迁移）

| 配置 | 默认 | 说明 |
|------|------|------|
| `FRONT_END_WEB_WATERMARK_ENABLED` | `false` | 总开关（复用既有键） |
| `FRONT_END_WEB_WATERMARK_TEXT` | `""` | 自定义文案；留空 = 用户名-昵称-时间 |
| `FRONT_END_WEB_WATERMARK_PATHS` | `""` | 生效页面路由前缀，逗号分隔；留空 = 全部页面 |

- 走既有 SysConfig 通道：`server/conf.py` 默认值 → `server/settings/setting.py` 映射 →
  `BasicSettingSerializer`（设置页自动出表单，无需前端改表单）→ 用户信息接口随 `config` 下发；
- **路径前缀匹配**（`route.path === prefix || startsWith`）：可写 `/system/user/index` 精确到页，
  也可写 `/system` 覆盖整目录；空范围 = 全部页面（与旧行为兼容）；
- **时间始终拼接在文案末尾**（自定义文案也保留时间），分钟粒度并每分钟刷新 —— 水印的价值是溯源。

### 2. 客户端：配置进 store，挂载/刷新收敛到 App.vue

- user store 只负责存 `siteWatermark = {enabled, text, paths}`，不再在 store 里挂水印；
  移除 `this.clear = clear` 状态劫持，`clear()` 改为真正的 action（复位水印配置，登出/清空缓存调用）；
- `src/utils/watermark.ts` 收敛为纯函数（路径解析 / 范围匹配 / 时间格式化 / 文案拼装），可单测；
- `src/App.vue` 观察「是否可见 + 文案 + 路由」，命中范围才挂载，离开范围/回到登录页即清除；
  分钟级刷新只在可见时更新文案（`setInterval` 卸载时清理），避免 DOM churn；
- 与设置面板的本地水印（`$storage.configure.watermark`）并存：站点水印命中范围时以站点水印文案为准，
  否则用本地文案 —— 两者语义不同（站点 = 管理侧强制，本地 = 个人偏好），不做合并开关。

### 3. 明示不做（评估出口）

- **不做菜单级/页面级开关**（原计划措辞的另一种读法）：需给 MenuMeta 加字段 + 迁移 + 菜单表单改造，
  而路由路径前缀配置已能覆盖「敏感页面」诉求，成本低一个量级；出现「同一目录下只对个别菜单生效且
  路径不稳定」的真实需求时再评估；
- **不做水印防篡改重挂载**（MutationObserver）：水印是威慑 + 溯源手段，不承担对抗 DOM 编辑/截图的职责；
- **不做导出文件水印**：归入导出/报表窗口评估。

### 4. 与既有实现的差异一览

| 维度 | 改造前 | 改造后 |
|------|--------|--------|
| 生效范围 | 全站（开关一开就全站） | 命中 `FRONT_END_WEB_WATERMARK_PATHS` 的页面（留空 = 全站） |
| 文案 | 用户名-昵称 | 用户名-昵称-时间（或自定义文案 + 时间） |
| 时间刷新 | 无 | 每分钟刷新（页面停留期间保持准） |
| 挂载点 | user store（登录时一次性） | App.vue 观察路由与配置（切页动态挂/清） |
| 配置入口 | 基本设置开关 | 基本设置三项（开关/文案/生效页面，自动出表单） |

## 测试与验收

- 服务端：`tests/integration/test_watermark_settings.py` —— 三项配置读写/持久化 + 用户信息接口下发
  （同源取值断言，避免写死默认值）；
- 前端：`src/utils/__tests__/watermark.spec.ts`（解析/范围/时间/文案）+ user store 单测（默认关闭、`clear()` 复位）；
- E2E：`e2e/watermark.e2e.ts` —— 开启 + 命中范围 → 用户管理页出现水印层且背景为 canvas 图；
  范围外页面不挂；空范围 = 全部页面；用例自建配置并在 `afterEach` 还原（水印是全屏固定层，防污染其它用例）；
- 门禁：pytest / ruff（check + format）/ i18n po（新增 4 条词条已补 en+zh 并重新编译 mo）/
  前端 typecheck / eslint / prettier / locale-keys。

## 增量（2026-09-18）：菜单级水印开关（触发条件命中）

原「不做菜单级/页面级开关」的重开条件是「同一目录下只对个别菜单生效且路径不稳定」的真实需求；
产品优先级确认后按需交付（长期优化方案 §4.5 水印行 / F4 按需功能池），本 ADR 同步修订结论。

- **数据**：`MenuMeta.watermark`（布尔，迁移 `0008_menu_meta_watermark`），经 `RouteMetaSerializer`
  随路由 meta 下发（`watermark` 只读）；
- **语义**：菜单级开关是「路径范围」的**补充而非替代**——`isSiteWatermarkVisible` 判定
  `总开关 && 非登录页 && (菜单开关 || 路径前缀命中)`，即置顶「页面强制挂载」；
- **入口**：菜单管理 → 基本信息 → 「页面水印」开关（zh/en 词条成对）；
- **仍不做**（维持原结论）：水印防篡改重挂载（MutationObserver）、导出文件水印；
- **测试**：服务端 `test_menu_api` / `test_routes_view`（meta 写回与透传）；前端
  `watermark.spec.ts`（或关系矩阵）；`e2e/watermark.e2e.ts` 新增「路径范围外页面由菜单开关
  强制挂载」用例（用例自建并在 `afterEach` 还原菜单开关与站点配置）。

## 增量（2026-10-03）：样式配置化 + 保存即生效 + 本地水印退役（真环境暴露问题）

真环境使用暴露三类问题：管理员把「生效页面」填成非路由文本（如中文说明）后，
所有页面匹配不上，水印**静默失效**且无任何反馈；保存后必须重新登录/刷新才生效；
设置面板本地水印与站点水印并存导致口径混乱。处置：

### 1. 生效页面保存期校验（防静默失效）

- `BasicSettingSerializer.validate_FRONT_END_WEB_WATERMARK_PATHS`：容忍中文逗号与空白，
  归一化为英文逗号列表；**每项必须以 `/` 开头**，否则保存直接 400 并回显非法项——
  把「配错→全页不挂→无人知晓」变成「保存时就得到明确报错」；
- 服务端为唯一校验点，客户端只做解析（`parseWatermarkPaths` 维持纯前缀匹配语义）。

### 2. 样式配置化：字号 / 透明度 / 旋转角（零新增表/迁移，仍走 SysConfig 通道）

| 配置 | 默认 | 说明 |
|------|------|------|
| `FRONT_END_WEB_WATERMARK_FONT_SIZE` | `16` | 字号（像素，8-72） |
| `FRONT_END_WEB_WATERMARK_OPACITY` | `0.3` | 透明度（0.01-1，越大越明显） |
| `FRONT_END_WEB_WATERMARK_ROTATE` | `-10` | 旋转角度（度，-90 到 90） |

- 随用户信息 `config` 一并下发；客户端 `toSiteWatermarkConfig` 解析并对非法值夹紧回默认，
  `buildWatermarkRenderOptions` 汇总为 `useWatermark` 渲染属性（App.vue 观察「可见 + 文案 +
  样式」三元组，样式变更同样触发重挂载）；文案 help_text 补充「可用『、』换行」的说明。

### 3. 保存即生效

- 管理员在基本设置保存含水印字段的表单后，前端经 `SettingItem.onSaved` 回调触发
  user store `refreshSiteWatermark()`（仅刷新水印配置），App.vue 随即重挂载——
  当前会话无需重新登录/刷新；其它在线用户仍在下次拉取用户信息时生效（口径不变）。

### 4. 设置面板本地水印退役

- 站点水印已覆盖「全站/范围强制水印」诉求，设置面板的本地「全屏水印」（每浏览器独立的
  `$storage.configure.watermark/watermarkText` + `platform-config.json` 的 `Watermark*` 键）
  整体移除：设置面板区块、storage 初始化、siteConfig 读写、类型声明、双语词条、
  种子数据（`loadjson/systemconfig.json` 的 `WEB_SITE_CONFIG` 内键）全部清理，
  App.vue 不再读本地水印——水印唯一口径 = 服务端基本设置（+ 菜单级开关补充）。

### 5. 测试

- 服务端：`test_watermark_settings.py` 扩至 7 例（路径归一化 / 非 `/` 开头拒绝 / 样式字段持久化）；
- 前端：`watermark.spec.ts` 增 config 解析与渲染属性用例；user / siteConfig spec 同步清理；
- 门禁：pytest / ruff / 前端 typecheck / eslint / vitest 全量。

## 增量（2026-10-03 其二）：文案模板化 + 颜色 + 独立页签 + 实时预览

### 1. 文案模板化（`FRONT_END_WEB_WATERMARK_TEXT` 语义升级，键名不变）

- 文案字段升级为**模板**：占位符 `{username}` `{nickname}` `{phone}` `{email}` `{pk}` `{time}`
  由前端按当前登录用户解析（userinfo 载荷已含全部字段）；
- 未知/缺失占位符替换为空串，连续「-」自动合并（昵称为空时默认模板不再出现双连字符）；
- 留空回落默认模板 `{username}-{nickname}-{time}`——旧行为（自定义文案原样 + 追加时间）
  的存量值作为「无占位符模板」继续生效，**零迁移**；
- 注意：模板不含 `{time}` 时水印不再自动带时间（溯源能力由配置者负责，help_text 已提示）。

### 2. 新增 `FRONT_END_WEB_WATERMARK_COLOR`（文字颜色）

- 十六进制（#rgb/#rrggbb/#rrggbbaa）/ rgb(a) / hsl(a) / CSS 颜色名，保存期正则校验
  （非法值在 canvas 里只会静默画默认色，配置时就拒绝）；留空 = useWatermark 默认灰；
- 与透明度（globalAlpha）叠加生效：color 决定色相，opacity 决定整体深浅。

### 3. 水印配置独立页签（前端拆分，后端接口不变）

- 基本设置页改为三页签：基本设置 / **水印设置** / 资源告警；
- 沿用 `settingItemProps.fields` 白名单机制（同 message 页渠道页签先例）：基本与水印
  两页签共用 `/api/settings/basic`，各自只渲染/提交自己的字段——不新增端点、
  不动权限位与菜单数据；
- 字段 label/说明统一由服务端 gettext 下发（前端不做字段级词条覆盖）。

### 4. 实时预览

- `WatermarkSetting.vue`：预览容器挂 `useWatermark(容器 ref)`，与 App.vue 共用同一套
  渲染属性（字号/透明度/旋转角/颜色/模板解析）——预览即所得；
- `SettingItem` 新增 `change` 事件（formData 深度 watch，含回显赋值），水印页防抖 200ms
  后重绘预览；占位符以当前登录管理员真实信息解析，离开页签即清除预览节点。

### 5. 测试

- 服务端：`test_watermark_settings.py` 扩至 10 例（颜色三形态合法 / 非法颜色拒绝 / 模板原样持久化）；
- 前端：`watermark.spec.ts` 扩至 15 例（模板解析 / 占位符缺失合并 / 颜色透传）；
- 门禁：pytest / ruff / typecheck / eslint / prettier / vitest 全量。

## 增量（2026-10-03 其三）：颜色选择器 + 数值步进元数据化

真环境反馈三条：颜色不应手输 hex（用户不知道色值）、透明度步进应为 0.1、
字段 label 服务端已下发不应在前端重复维护。处置全部走**元数据驱动**而非页面特判：

- **颜色选择器**：COLOR 字段 `CharField` → 框架既有 `ColorField`（input_type="color"，
  词表与客户端渲染器早已登记）——search-columns 下发 color，前端渲染 el-color-picker
  （showAlpha + 预置色板），保存期校验保留；
- **数值边界/步进元数据化**：search-columns 载荷补 `step`（新增 `StepFloatField` 显式声明，
  通用扩展，不绑定水印）；`min_value`/`max_value` 是 DRF 标准属性本就随
  SimpleMetadata 下发，只是此前无人消费——前端 `numberFormRenderer` 统一映射为
  el-input-number 的 min/max/step（未下发即缺省，全平台数值表单受益，非水印专属）；
  OPACITY 声明 step=0.1；契约 `docs/schema/search-columns.schema.json` 补三键锁步，
  客户端 `pnpm sync:contract` 重生成类型；
- **label 回归服务端**：删除前端 `settingWatermark.FRONT_END_WEB_WATERMARK_*` 词条，
  仅保留页签标题与预览文案（与 `settingBasic.title` 同模式的页面级 UI 词条）。

测试：`test_watermark_settings.py` 扩至 11 例（search-columns 元数据断言：
color input_type / opacity step=0.1 与 min/max / 字号边界）；
真环境 Redis 元数据载荷缓存（5 分钟 TTL）已手动失效，改动即时可见。
