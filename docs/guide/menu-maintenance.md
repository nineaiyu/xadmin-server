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
| DataDashboard | `/analysis/dashboard/index` | `dashboard/index` | 历史口径（与 DataDataset 同源） |
| DataDataset | `/analysis/dataset/index` | `dashboard/dataset/index` | menu.json 该行 description 有同文护栏 |

## 4. 新增页面规范

- 新页面：URL 与 `src/views/` 目录保持一致（参照 DataReport/DataScreen）；
- 需要偏离时：先在本文第 3 节登记例外与理由，再改种子；
- 菜单 description 字段（≤256 字符）可用于在该行种子上留下护栏说明。
