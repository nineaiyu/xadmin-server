# 菜单维护口径（种子 / 路径 / 映射）

> 本文是菜单种子（`loadjson/menu.json`）与前端路由目录关系的维护口径登记处，
> 防止后续"顺手修齐"造成断链。权限点双向对账 CI 门禁落地时，
> 以本文登记的例外清单为白名单依据。

## 1. 基本口径

- 菜单种子 `loadjson/menu.json` 是唯一权威：`path` = 前端路由 URL，
  `component` = `src/views/` 下组件相对路径。二者**默认相等**（URL=目录），
  不相等属历史显式映射，必须登记于本文第 3 节例外清单，禁止未登记就改。
- 种子经 `load_init_json`（loaddata 按 pk 覆盖）落库；线上库菜单行已按既有
  `path` 落库并被角色授权引用，**改种子的 path 不会自动迁移线上数据**，
  旧 URL 上的书签/收藏/历史/外部链接会全部断链。

### 1.1 例外：demo 模块的菜单与权限点是运行期注入

`demo`（示例 app）的菜单与权限点**不在 `loadjson/menu.json` 里**：由
`python manage.py seed_demo_book`（`demo_seed/management/commands/seed_demo_book.py`）
在运行期幂等灌入（`update_or_create`：目录「示例」+ 页面「图书管理」+
`PERMISSION_PLAN` 的 19 个权限点，含 `batchDestroy` / 回收站 / 导入导出等
`generate_crud` 默认种子之外的 action）。口径上它仍是"种子"，只是载体是命令而非 JSON：

- **对账覆盖**：客户端对账门禁 `xadmin-client/scripts/check-menu-permissions.mjs`
  除解析 `loadjson/menu.json` 外，还解析 `seed_demo_book.py` 的 `MENU_NAME` /
  `PERMISSION_PLAN` 作为第二种子源，demo 页面的权限检查（`push:DemoBook` 等）
  不会按"种子无码"误报；
- **与 loadjson 的关系**：`loadjson/menu.json` 仍是平台内置菜单的唯一权威，
  demo 注入是该口径唯一的运行期例外；其余业务 app 一律按 §4 新增页面规范
  走平台种子或 `generate_crud` 生成的 `loadjson/seed_*.json`。

## 2. 分析域 URL≠目录 映射（口径结论，2026-10）

**决策：保留映射，不修齐。**

"数据分析"菜单区（父菜单 `fc447510-2820-524c-a802-d7241bd71e5f`）4 个页面中，
2 个 URL=目录、2 个 URL≠目录：

| 菜单 | URL (path) | 组件 (component) | URL=目录 |
|---|---|---|---|
| DataDashboard | `/analysis/dashboard/index` | `dashboard/index` | ✗ |
| DataDataset | `/analysis/dataset/index` | `dashboard/dataset/index` | ✗ |
| DataReport | `/analysis/report/index` | `analysis/report/index` | ✓ |
| DataScreen | `/analysis/screen/index` | `analysis/screen/index` | ✓ |

不修齐的理由：

1. 线上库菜单行已按 `/analysis/dataset/index` 落库并被角色绑定；改 URL 需要数据
   迁移配合，否则升级即断链（"统一 URL=目录"不是改一行种子那么简单）。
2. 改组件目录（`views/dashboard/dataset/` → `views/analysis/dataset/`）对用户
   零收益，却要动 import 面、e2e 与种子三处，回归面大于收益。
3. 仪表盘页 `views/dashboard/index.vue` 的"数据集"跳转
   （`goDatasetPage`）依赖该 URL，两处已加护栏注释。

**约束**：任何人想"统一口径"前，必须先在本文更新决策并按第 3 节流程做迁移
方案评审；禁止在无迁移方案的情况下改种子 path 或移动组件目录。

## 3. 例外清单（URL≠目录 显式映射，对账门禁白名单）

| 菜单 name | URL | 组件 | 登记来源 |
|---|---|---|---|
| UserInfo | `/user/info/index` | `account/index` | 历史口径：个人中心 URL 与组件目录独立演化 |
| DataDashboard | `/analysis/dashboard/index` | `dashboard/index` | 历史口径（与 DataDataset 同源，见 §2） |
| DataDataset | `/analysis/dataset/index` | `dashboard/dataset/index` | menu.json 该行 description 有同文护栏（见 §2） |
| FormDesigner | `/form-collection/designer/index` | `form/designer/index` | 历史口径：表单域 URL 前缀 `/form-collection`，组件在 `form/` 下 |
| FormMySubmission | `/form-collection/my/index` | `form/my/index` | 历史口径：同 FormDesigner |
| FormData | `/form-collection/data/index` | `form/data/index` | 历史口径：同 FormDesigner |
| AiMcpServers | `/integration/ai/mcp` | `integration/ai/mcp/index` | 历史口径：URL 不带 `/index` 后缀 |

## 4. 新增页面规范

- 新页面：URL 与 `src/views/` 目录保持一致（参照 DataReport/DataScreen）；
- 需要偏离时：先在本文第 3 节登记例外与理由，再改种子；
- 菜单 description 字段（≤256 字符）可用于在该行种子上留下护栏说明。

## 5. 对账白名单（预期偏差登记）

权限点双向对账门禁（`xadmin-client/scripts/check-menu-permissions.mjs`）的登记处。
方向 **A** = 种子有权限点、前端无消费证据；方向 **B** = 前端有权限检查、种子无对应码。
登记后该项跳过对账；新增条目必须注明理由。**机器事实源是门禁脚本的
`WHITELIST_A` / `WHITELIST_B` / `URL_EXCEPTIONS` 清单，本表为同步登记的人类可读版，
两处需同步更新。**

| 权限点 | 方向 | 理由 |
|---|---|---|
| `retrieve:Spectacular` | A | 文档外链类：API 文档页由菜单链接直达，前端无 hasAuth 校验位属预期 |
| `retrieve:SpectacularSwaggerView` | A | 文档外链类：同上（Swagger UI） |
| `retrieve:SpectacularRedocView` | A | 文档外链类：同上（Redoc） |
| `retrieve:SystemFlower` | A | 文档外链类：Celery Flower 监控页外链打开 |
| `create:SystemFlower` | A | 文档外链类：同上（该点仅配对方法位存在，无前端交互） |
| `enable:SystemTask` | A | 前端任务启停走批量端点（batch-enable / partialUpdate），单任务 enable 端点无前端交互 |
| `syncRepoStatus:AiKnowledge` | A | 前端只调 sync-repo 触发同步，状态经列表刷新获得，status 查询端点无前端消费 |
| `update:SystemApprovalFlow` | B | 编辑兼容口径：前端 OR 检查 update/partialUpdate（两页编辑保存均走 partialUpdate，权限授予习惯不同，只认其一会让另一类角色看不到编辑入口），种子只授 partialUpdate |
| `update:SystemApprovalRule` | B | 编辑兼容口径：同 SystemApprovalFlow |

另：`retrieve:SystemGlobalSearch` 与 `list:SystemImportTemplate` 在门禁脚本的
`MUST_CODE_EVIDENCE` 清单中——这两处曾"端点有人调、权限没人查"，后补的前端
hasAuth 校验不得回退删除。

## 6. 权限码语义口径（页签显隐 / 同端点多权限点）

页签类功能无独立路由菜单，其显隐/按钮权限码挂在宿主页面菜单下；同端点挂多个
权限点属于**有意的授权粒度设计**，维护时不得按"疑似重复"清理：

| 权限码 | 口径 |
|---|---|
| `list:SystemImportRecord` | 下载中心「导入记录」页签显隐开关（`views/system/export/index.vue`），挂在 SystemExportRecord 菜单下；权限点标题已注明「控制导入页签显隐」 |
| `retrieve/partialUpdate:SettingWatermark` | 基本设置「水印设置」页签独立权限位，与 `SettingBasic` 两点同 path（`api/settings/basic$`）：后端按 path+method 鉴权为 OR 语义（任一点授权即可调 API），前端页签按 Watermark 码独立判权——仅授权粒度拆分，不做字段级隔离；存量自定义角色需显式勾选后水印页签才可见 |
| `cancel/rerun:SystemTaskExecution` | 执行历史页取消/重跑按钮，走聚合端点 `/api/system/tasks/unified/{cancel,rerun}`；权限码归执行历史资源名（2026-10 由 `SystemTaskCenter` 改名，任务中心菜单已删除，pk 未变故存量授权自动延续） |
