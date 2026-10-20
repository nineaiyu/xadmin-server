# ADR-097：数据权限显式 action 覆盖与 list 缺省回退

- 状态：**已交付（2026-10-20）**
- 日期：2026-10-20
- 关联：[ADR-056](ADR-056-data-permission-config-and-scope.md)（数据权限配置页与生效范围归一）、
  `docs/architecture/data-permission.md`（现状口径）、`common/core/data_scope/`（规则编译器）

## 一、背景

非超管用户在角色管理页点击「授权预览」时，接口返回
`400 请求地址不正确或数据权限不允许`，但同一行的列表接口正常、按钮权限点也已授予。

定位结论：

1. `request.user.menu` 在预览请求中解析为 `preview:SystemRole`；数据权限授权
   「本部门只读权限」只绑定了 `list:SystemRole` 等读接口，没有绑定 `preview:SystemRole`。
   授权池按菜单精确匹配，预览拿不到任何适用授权，`get_filter_queryset` 返回 `none()`，
   `get_object()` 抛 `Http404`，全局异常处理再映射为 400。
2. 演示种子「演示只读数据范围」中角色表写成了领域拆分前的 `system.userrole`
   （当前模型为 `identity.userrole`），规则编译按模型 label 精确匹配，整条规则静默失效；
   巡检命令可识别该非法表名。
3. 第一版修复采用「list 授权无条件并入兄弟动作」，解决了预览 400，但带来新问题：
   一旦 list 与 detail 都配置了规则，detail 会同时叠加两套规则（并集取最宽），
   无法表达「detail 严格按自己的规则」；同时每新增一个子 action 仍要回填所有存量授权。

## 二、决策

数据权限的菜单范围改为「显式 action 覆盖 + 同资源 list 缺省回退」：

| # | 决策 | 要点 |
|---|------|------|
| 1 | 通用授权始终参与 | `menu` 为空的授权不参与覆盖判定，任何 action 都叠加 |
| 2 | 显式 action 覆盖 | 当前用户的生效授权池中存在**绑定当前 action 且对当前模型可编译**的授权时，只使用「通用 + 当前 action」，同资源 list 授权不参与 |
| 3 | list 缺省回退 | 当前 action 没有可用显式授权时，使用「通用 + 同资源 list 权限点」；「列表能看到的行才能打开详情」 |
| 4 | 严格互不影响 | 显式配置 detail 后，list 规则不再影响 detail；detail 可以比 list 更窄，代价是「列表可见但详情 404」成为合法状态 |
| 5 | 精确 action 粒度 | `retrieve`、`preview` 是两个独立权限点；页面/目录勾选保存时展开为权限点，配置一次可覆盖多个 action |
| 6 | 可编译才算已配置 | 仅绑定当前 action 但规则只针对其它模型的授权不参与覆盖判定，不掐掉 list 回退 |
| 7 | 字段权限不变 | 字段权限仍按 `(role, menu)` 精确匹配，未配置 = 零字段（fail-closed）；不做隐式 list 继承（避免把列表列自动暴露到详情） |
| 8 | 选中集合内部语义不变 | 组内按授权自身且/或模式，授权之间并集取最宽；通用授权只放宽、不收紧 |

实现落点：

- 新增 `common/core/data_scope/menu_scope.py::MenuScope`：
  `exact_pks`（当前 action/页面下接口）+ `fallback_pks`（同资源 list）；
- `resolve_menu_scope` / `resolve_scope_for_user`：权限点按 `action:Resource` 推导 list；
  页面/目录解析为其下 `list:*` 权限点（试算上下文）；
- `select_compiled_grants`：先编译候选授权，再按「exact 有 applicable → exact；
  否则 fallback」选择；通用授权始终保留；
- `common/core/filter.py::_load_grants` 缓存并返回每条授权的 `menu_ids`，
  `get_filter_queryset` 用 `select_compiled_grants` 决定参与合并的授权；
- `system/utils/platform/permission_preview/trial_data.py` 复用同一选择逻辑；
  草稿页面绑定按保存时的展开口径归一为权限点；试算返回 `scope_mode`
  （exact / list / generic）与每条授权的 `selected` 标记；
- 菜单解析失败/非 `action:Resource` 命名（自定义页面、非 ViewSet 端点）回落为仅当前菜单，
  保持旧口径与 fail-closed 边界。

## 三、影响与边界

- **只配置 list**：detail/preview 回退 list，修复原 400 问题。
- **list 与 detail 都配置**：detail 从第一版的「两者并集」改为「只认 detail」；
  若 detail 更窄，会出现「列表能点开但详情 404」。这是严格互不影响的预期代价，
  上线前应复核同时绑定 list 与 detail 的授权。
- **detail 规则只针对其它模型**：不视为已配置，继续回退 list（避免无关规则造成空集）。
- **通用授权**：始终参与并集；显式 action 规则无法收紧通用授权（授权只放宽）。
- **字段权限**：完全不受影响；列表与详情可配置不同字段集合，未配置 detail 不会自动继承 list 字段。
- 一次非超管请求增加 1~2 次菜单查询（list 动作查一次菜单名，详情/预览再查一次 list 主键）；
  结果按请求缓存，授权池缓存键纳入菜单候选集合，不引入新的全局缓存与失效链路。
- 试算的菜单上下文仍是页面菜单；页面上下文按「列表请求」模拟，`retrieve`/`preview`
  的精确覆盖需在对应 action 上单独配置（页面勾选保存会展开）。

## 四、验证

- `tests/unit/common/test_data_scope_menu.py`：`MenuScope` 解析、
  `select_compiled_grants` 的 exact 覆盖 / list 回退 / 非适用 exact 不阻断回退 / 通用保留；
- `tests/unit/common/test_data_permission_filter.py`：显式 action 覆盖 list、
  只绑 list 时兄弟动作回退、跨资源隔离、显式规则不适用时回退；
- `tests/integration/system/test_role_preview_scope.py`：非超管角色预览在 list 回退下 200，
  显式 preview 规则覆盖 list 后不匹配对象 400；
- `tests/integration/system/test_permission_preview.py`：试算页面上下文命中 list 绑定授权，
  `scope_mode` / `selected` 诊断；
- `tests/unit/common/test_serializer_field_permission.py`：list/detail 字段权限保持独立；
- `tests/unit/demo_seed/test_demo_permissions.py`：规则表名必须是当前应用注册表中的模型 label。
