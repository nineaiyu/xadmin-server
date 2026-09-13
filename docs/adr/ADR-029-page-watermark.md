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
