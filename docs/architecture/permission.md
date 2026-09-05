# 三层权限体系设计（API / 数据 / 字段）

> 适用版本：xadmin-server 4.2.5+（T2.6 梳理，2026-09-05）
> 本文是三层权限的**整体设计文档**；操作教程见 [data-permission.md](../data-permission.md) 与 [field-permission.md](../field-permission.md)。

## 一、总览

xadmin 的权限模型由三层组成，在一次 HTTP 请求中按以下顺序生效：

```
请求 → ① API 权限（能不能访问这个接口）
     → ② 数据权限（能访问哪些行）
     → ③ 字段权限（能序列化出哪些列）
     → 视图处理 → 响应
```

| 层 | 入口 | 核心模块 | 粒度 | 载体 |
|----|------|----------|------|------|
| ① API/菜单权限 | DRF `DEFAULT_PERMISSION_CLASSES` | `common/core/permission.py` | URL × Method | `Menu`（menu_type=PERMISSION）× `UserRole` |
| ② 数据权限 | `DEFAULT_FILTER_BACKENDS` | `common/core/filter.py` | 表的行 | `DataPermission`（规则 JSON）× 角色/用户 |
| ③ 字段权限 | `BaseModelSerializer.__init__` | `common/core/serializers.py` | 表的列 | `FieldPermission`（角色 × 菜单）× `ModelLabelField` 树 |

三条层共享同一套角色-用户-部门关系（`UserInfo → UserRole → Menu`，部门可挂角色），并共享 `MagicCacheData` 缓存体系与信号失效链路。

**超管旁路**：`is_superuser=True` 的用户三层全部跳过（`request.ignore_field_permission = True`）。

## 二、第一层：API / 菜单权限

### 2.1 授权链路

```
用户 ──直接/经部门──> 角色(UserRole) ──> 菜单(Menu, menu_type=PERMISSION)
```

- `get_user_menu_queryset(user)`：用户所有角色（含部门挂载的角色）关联的启用菜单。
- `get_user_permission(user, method)`：按请求方法过滤出 `menu_type=PERMISSION` 的菜单，产出 `{path: (menu_pk, 绑定模型)}` 映射，缓存 24h。
- `IsAuthenticated.has_permission`：用 `request.path_info` 在映射中匹配。

### 2.2 URL 匹配规则（`get_menu_pk`）

1. **精确匹配**：`path + "$"`（如菜单 path 配 `api/system/permission$`）；
2. **前缀正则**：按 `/{p_path}` 逐项 `re.match`，命中即返回；
3. **特例重定向**：
   - `/search-columns` 与所属资源共享 list 权限（元数据接口不单独授权）；
   - `/import-data`、`/export-data` 在菜单**未绑定模型**时，回退匹配去掉后缀的 list / create 菜单。

### 2.3 关键行为

- **白名单**：`settings.PERMISSION_WHITE_URL`（正则 → 方法集合），命中则完全跳过权限。
- **fail-closed**：权限缓存或 DB 查询抛异常时返回 403，绝不放行（PERF-01 修复项）。
- **字段权限开关**：`settings.PERMISSION_FIELD_ENABLED` 为 False 时跳过第三层。
- 通过校验后，`request.user.menu` 被注入当前菜单（供第二/三层与导入导出复用），`request.fields` 注入字段权限集合。

## 三、第二层：数据权限

### 3.1 原理

`BaseDataPermissionFilter.filter_queryset` 在**每个列表/详情查询**上追加 `queryset.filter(...)`：
`get_filter_queryset(queryset, user)` 汇总用户身上所有 `DataPermission`（用户直接挂 `rules` + 经角色挂 `rules`），按 AND/OR 模式构造 Q 对象。

- **且模式（AND）**：行必须满足规则列表中的每一条；
- **或模式（OR）**：满足任意一条即可（`ModeTypeAbstract.ModeChoices`）。
- 规则可绑定菜单：仅对所选菜单对应的接口生效。
- 部门维度的规则依赖 `DeptInfo.recursion_dept_info` 展开部门树。

### 3.2 规则类型速查表（`ModelLabelField.KeyChoices`，共 14 种）

| 类型值 | 含义 | value 形态 |
|--------|------|-----------|
| `value.text` | 文本匹配 | 字符串 |
| `value.json` | JSON 匹配 | JSON |
| `value.all` | 放行全部数据 | `*` |
| `value.datetime` | 距当前时间（秒） | 整数 |
| `value.datetime.range` | 时间范围选择器 | 区间 |
| `value.date` | 距当前时间（秒，date 精度） | 整数 |
| `value.user.id` | 「我的」数据（当前用户 ID） | `*` |
| `value.user.dept.id` | 本部门数据 | `*` |
| `value.user.dept.ids` | 本部门及下级部门数据 | `*` |
| `value.dept.ids` | 指定部门及下级 | 部门 ID 列表 |
| `value.table.user.ids` | 指定用户集合 | 用户 ID 列表 |
| `value.table.menu.ids` | 指定菜单集合 | 菜单 ID 列表 |
| `value.table.role.ids` | 指定角色集合 | 角色 ID 列表 |
| `value.table.dept.ids` | 指定部门集合（不含展开） | 部门 ID 列表 |

规则结构（`DataPermission.rules`，JSON 列表）：

```json
[{"table": "demo.book", "field": "admin", "type": "value.user.id", "value": "*", "match": "exact"}]
```

> 注：历史文档口径为「12 种规则」，以代码为准共 **14 种**（含 `value.table.*` 四种）。

### 3.3 生效范围

- 全局 `DEFAULT_FILTER_BACKENDS` 自动对所有 ViewSet 生效；视图内用 `self.filter_queryset(self.get_queryset())` 显式触发。
- 导入（update 分支）、批量删除等写路径同样走 `filter_queryset`，因此**越权写同样被数据权限拦截**。
- 权限缓存与数据权限解耦：数据权限变更走 `DataPermission` 相关信号与角色失效链路。

## 四、第三层：字段权限

### 4.1 原理

```
FieldPermission(角色 × 菜单) ──> ModelLabelField 树（模型 → 字段）
BaseModelSerializer.__init__ 读取 request.fields，裁剪 serializer.fields
```

- `get_user_field_queryset(user, menu)`（缓存 10s）：汇总该用户在当前菜单下的字段授权，产出 `{模型名: {字段名集合}}`。
- `BaseModelSerializer` 在实例化时按 `request.fields` 保留/剔除字段，未配置字段权限的用户输出为空集（需显式授权，见 field-permission.md）。
- 跳过条件：超管、白名单 URL、`PERMISSION_FIELD_ENABLED=False`（`ignore_field_permission`）。
- 单个字段可通过 `ignore_field_permission=True`（extra_kwargs）豁免，如文件回显字段。

### 4.2 与 search-columns 的联动

元数据接口（search-columns）复用同一套字段权限，前端表单/表格列随用户权限动态收敛——**列隐藏是服务端裁剪的结果，不是前端遮挡**。

## 五、缓存与失效

### 5.1 缓存 key 一览（`common/base/magic.py`）

| 数据 | key 形态 | TTL |
|------|----------|-----|
| API 权限映射 | `get_user_permission_{user_pk}_{method}` | 24h |
| 字段权限集合 | `get_user_field_queryset_{user_pk}_{menu_pk}` | 10s |
| 响应缓存（list/retrieve） | `{视图}_{方法}_{user_pk}[_query_hash]` | 视图配置 |

### 5.2 信号失效链路（`system/signal_handler.py`）

| 触发源 | 信号 | 动作 |
|--------|------|------|
| Menu / SystemConfig / UserRole / DeptInfo / UserInfo 变更 | `post_save` / `pre_delete` | 批量失效相关用户权限缓存 |
| `UserRole.menu` / `UserInfo.roles` / `DeptInfo.roles` 变更 | `m2m_changed` | 同上（覆盖 ORM 直改 M2M 场景） |
| 用户登出 / 显式踢出 | `user_logged_out` / `invalid_user_cache_signal` | 失效该用户权限缓存 |

变更**立即生效**（信号驱动），不依赖 TTL 过期；TTL 仅作兜底。

## 六、调试指引

### 6.1 常见问题排查

| 现象 | 排查步骤 |
|------|----------|
| 403 但用户确有角色 | 1) 检查菜单 `is_active` 与 `menu_type`；2) 检查角色/部门挂载关系；3) 打印 `get_user_permission(user, method)` 结果；4) 确认 URL 匹配（精确 `$` 后缀 vs 前缀正则） |
| 改了角色/菜单权限未生效 | 确认请求经 API 发起（信号已挂 `m2m_changed`）；若绕过 ORM 直改，触发 `system.signal_handler.clean_cache_handler` 或调 `MagicCacheData.invalid_caches` |
| 列表有数据但详情 403 | 数据权限规则绑定了菜单，检查规则所挂菜单是否覆盖详情路由 |
| 字段输出比预期少 | 1) `request.fields` 是否被 FieldPermission 收敛；2) 字段所在模型是否在 `ModelLabelField` 树中正确挂载；3) 前端传参字段与后端字段名大小写 |
| 导入/导出 403 | 未绑定模型的菜单回退到 list/create 权限——检查菜单的「绑定模型」配置 |

### 6.2 Shell 验证片段

```python
from django.contrib.auth import get_user_model
from common.core.permission import get_user_permission, get_user_field_queryset

user = get_user_model().objects.get(username="xxx")
get_user_permission(user, "GET")                 # 该用户全部 API 权限映射
get_user_field_queryset(user, menu)              # 指定菜单下字段权限
user.rules.all()                                 # 用户直接挂载的数据权限
[user_role.rules.all() for user_role in user.roles.all()]  # 经角色挂载的数据权限
```

### 6.3 日志关键字

- `get user permission failed`：权限查询异常（fail-closed 403）；
- `invalid_cache_data cache_key:magic_cache_data_get_user_permission`：权限缓存失效轨迹；
- `drf_exception`：权限类异常统一落点（含 requestId，可全链路追踪）。

## 七、测试地图

| 行为 | 测试 |
|------|------|
| 信号失效真实链路 + m2m_changed | `tests/unit/system/test_signal_handler.py` |
| API 权限 fail-closed / 白名单 | `tests/unit/common/test_core_permission.py` |
| 数据权限 12+ 种规则过滤 | `tests/unit/common/test_data_permission_filter.py`、`test_dept_tree_cache.py` |
| 字段权限裁剪 | `tests/unit/common/test_serializer_field_permission.py` |
| 三层联动端到端（越权验证） | `tests/integration/demo/test_book_viewset.py::TestBookDataPermissionIntegration` |

## 八、已知限制与设计取舍

- 菜单权限经角色控制后**不再**叠加数据权限过滤（避免双重配置，见 `get_user_menu_queryset` 注释）——给角色配数据权限即可，无需两处都配。
- `get_user_permission` 缓存 24h 是性能取舍，正确性由信号失效保证；若新增绕过 ORM 的写路径，必须补信号或手动失效。
- `request.fields` 在权限中间件注入，非 DRF 场景（WebSocket 等）需自行处理字段权限。
