# 权限体系设计（API / 数据 / 字段 + 应用级授权）

> 适用版本：xadmin-server 5.0.0+（T2.6 梳理，2026-09-05；2026-09 增补第四道「应用级授权」）
> 本文是权限体系的**整体设计文档**；原独立操作教程已清理，数据 / 字段权限的配置与排障以本文为准。

## 一、总览

xadmin 的权限模型由三层组成；开放平台 API 应用以 PAT（个人访问令牌）凭证访问时，在其上额外叠加
第四道「应用级授权」收敛（模型 × 动作 × 字段 × 行）。在一次 HTTP 请求中按以下顺序生效：

```
请求 → ① API 权限（能不能访问这个接口）
     → ② 数据权限（能访问哪些行）
     → ③ 字段权限（能序列化出哪些列）
     → 视图处理 → 响应
        （PAT 应用凭证下：模型×动作判定 → 行级 AND → 字段级 AND，三道只收紧不提权，见 §2.4）
```

| 层          | 入口                               | 核心模块                         | 粒度           | 载体                                              |
|------------|----------------------------------|------------------------------|--------------|-------------------------------------------------|
| ① API/菜单权限 | DRF `DEFAULT_PERMISSION_CLASSES` | `packages/xadmin-common/common/core/permission.py`  | URL × Method | `Menu`（menu_type=PERMISSION）× `UserRole`        |
| ② 数据权限     | `DEFAULT_FILTER_BACKENDS`        | `packages/xadmin-common/common/core/filter.py`      | 表的行          | `DataPermission`（规则 JSON）× 角色/用户                |
| ③ 字段权限     | `BaseModelSerializer.__init__`   | `packages/xadmin-common/common/core/serializers.py` | 表的列          | `FieldPermission`（角色 × 菜单）× `ModelLabelField` 树 |
| ④ 应用级授权    | 权限类 + 过滤后端 + 序列化层三处挂载       | `identity/utils/api_grant.py`  | 模型 × 动作 × 字段 × 行 | `ApiApplication.grant`（开放平台 API 应用，仅 PAT 凭证）   |

前三层共享同一套角色-用户-部门关系（`UserInfo → UserRole → Menu`，部门可挂角色），并共享 `MagicCacheData` 缓存体系与信号失效链路；第四层挂在应用凭证维度，与前三层取交集。

**超管旁路**：`is_superuser=True` 的用户前三层全部跳过（`request.ignore_field_permission = True`）；但应用授权在超管 + PAT 凭证场景下仍生效（`apply_grant_row_scope` 对 owner 同样收敛）。

## 二、第一层：API / 菜单权限

### 2.1 授权链路

```
用户 ──直接/经部门──> 角色(UserRole) ──> 菜单(Menu, menu_type=PERMISSION)
```

- `get_user_menu_queryset(user)`：用户所有角色（含部门挂载的角色）关联的启用菜单。
- `get_user_permission(user, method)`：按请求方法过滤出 `menu_type=PERMISSION` 的菜单，产出 `{path: (menu_pk, 绑定模型)}`
  映射，缓存 24h。
- `IsAuthenticated.has_permission`：用 `request.path_info` 在映射中匹配。

### 2.2 URL 匹配规则（`get_menu_pk`）

1. **精确匹配**：`path + "$"`（如菜单 path 配 `api/system/permission$`）；
2. **前缀正则**：按 `/{p_path}` 逐项 `re.match`，命中即返回；
3. **特例重定向**：
    - `/search-columns` 与所属资源共享 list 权限（元数据接口不单独授权）；
    - `/import-data`、`/export-data` 在菜单**未绑定模型**时，回退匹配去掉后缀的 list / create 菜单。
4. **同端点双方法回退**：一个 `@action(methods=["get", "post"])` 的 handler 在权限点生成器里
   只产出一个权限码（如 `members:ChatRoom`），但运行时按 `(method, path)` 取权限。当前方法
   没有独立权限点时，按视图 `action_map` 回退到**同 handler 兄弟方法**的权限点——只认同尾部
   路径（`/members$`），不做前缀放宽（见 `_shared_method_menu_pk`）。新增此类端点无需登记；
   `system/services/permission_sync/constants.py::SHARED_METHOD_PATHS` 只服务扫描器的缺口口径。

### 2.3 关键行为

- **白名单**：`settings.PERMISSION_WHITE_URL`（正则 → 方法集合），命中则完全跳过权限。
  - 命名提示：`PERMISSION_WHITE_REURL` 是各业务 app 的 `config.py` 导出的扩展变量（形如
    `PERMISSION_WHITE_REURL = [("^/api/demo/", ["GET", "POST"])]`），由 `packages/xadmin-common/common/core/url_inventory.py`
    的 `auto_register_app_url` 在注册路由时合并进 `settings.PERMISSION_WHITE_URL`——运行时真正直读的
    只有后者。
- **fail-closed**：权限缓存或 DB 查询抛异常时返回 403，绝不放行（PERF-01 修复项）。
- **字段权限开关**：`settings.PERMISSION_FIELD_ENABLED` 为 False 时跳过第三层。
- 通过校验后，`request.user.menu` 被注入当前菜单（供第二/三层与导入导出复用），`request.fields`
  由权限类 `IsAuthenticated._load_field_permission` 注入字段权限集合（非中间件）。

### 2.4 应用级授权（开放平台 API 应用，第四道收敛）

仅供以 **PAT 应用凭证**（`request.auth` 绑定 `ApiApplication`）访问的请求生效；JWT / 匿名请求不适用。
实现见 `identity/utils/api_grant.py`，由三处挂载叠加在原有三层之上：

| 维度 | 挂载点 | 行为 |
|------|--------|------|
| 模型 × 动作 | `IsAuthenticated`（`enforce_application_grant`） | 白名单模式：应用存在生效规则时必须命中「模型（精确或 `*`）× 动作（或 `*`）」，否则 `PermissionDenied` |
| 行级 | `BaseDataPermissionFilter`（`apply_grant_row_scope`） | `row_filter` 编译为 `Q`，AND 叠加在数据权限过滤之后（超管 owner 同样收敛） |
| 字段级 | `BaseModelSerializer`（`apply_grant_fields`） | `fields` 非空时与用户字段权限取交集，且穿透字段权限豁免 |

语义红线：**只收紧不提权**，全部与前三层取交集；应用无生效规则时为兼容模式（判定跳过，存量接入零影响）。

## 三、第二层：数据权限

### 3.1 原理

`BaseDataPermissionFilter.filter_queryset` 在**每个列表/详情查询**上追加 `queryset.filter(...)`：
`get_filter_queryset(queryset, user)` 汇总用户身上所有 `DataPermission`（个人直接绑定 + 所在部门及其祖先链上的
部门绑定，仅启用部门生效），池内各授权组统一「或」合并（取最宽生效），组内多规则按授权自身模式（AND/OR）组合为 Q 对象。

- **且模式（AND）**：行必须满足规则列表中的每一条；
- **或模式（OR）**：满足任意一条即可（`ModeTypeAbstract.ModeChoices`）。
- 规则可绑定菜单：当前 action 有可编译的显式授权时只用它，未配置时回退同资源 `list` 权限点；
  显式 action 与 list 互不影响；通用授权始终参与（授权只放宽、不改变动作权限）。
- 部门维度的规则依赖 `DeptInfo.recursion_dept_info` 展开部门树。

### 3.2 规则类型速查表（`ModelLabelField.KeyChoices`，共 18 种）

| 类型值                    | 含义               | value 形态 |
|------------------------|------------------|----------|
| `value.text`           | 文本匹配             | 字符串      |
| `value.json`           | JSON 匹配          | JSON     |
| `value.all`            | 放行全部数据           | `*`      |
| `value.datetime`       | 绝对时间点（解析时间字符串）     | 时间字符串      |
| `value.datetime.range` | 时间区间（两个绝对时间点）       | `[起, 止]`   |
| `value.date`           | 距当前时间的秒偏移（负=过去，正=未来） | 整数 / 小数    |
| `value.user.id`        | 「我的」数据（当前用户 ID）  | `*`      |
| `value.user.dept.id`   | 本部门数据            | `*`      |
| `value.user.dept.ids`  | 本部门及下级部门数据       | `*`      |
| `value.dept.ids`       | 指定部门及下级          | 部门 ID 列表 |
| `value.leader.dept.ids` | 我主管部门及全部下级部门     | `*`（按绑定用户解析） |
| `value.leader.user.ids` | 我主管部门及下级部门的成员    | `*`（按绑定用户解析） |
| `value.manager.dept.ids` | 我管理的部门及全部下级部门（部门管理员） | `*`（按任命关系解析） |
| `value.manager.user.ids` | 我管理部门的成员（部门管理员）    | `*`（按任命关系解析） |
| `value.table.user.ids` | 指定用户集合           | 用户 ID 列表 |
| `value.table.menu.ids` | 指定菜单集合           | 菜单 ID 列表 |
| `value.table.role.ids` | 指定角色集合           | 角色 ID 列表 |
| `value.table.dept.ids` | 指定部门集合（不含展开）     | 部门 ID 列表 |

规则结构（`DataPermission.rules`，JSON 列表）：

```json
[{"table": "demo.book", "field": "admin", "type": "value.user.id", "value": "*", "match": "exact"}]
```

> 注：历史文档口径为「12 种」；`value.leader.*`（部门主管，2026-09 部门主管链路交付）、`value.table.*`
> 与 `value.manager.*`（部门管理员，ADR-077）为后续增量，以代码为准共 **18 种**。**部门主管 / 部门管理员
> 类规则**：不是主管部门主管 / 未获管理任命的用户解析为空列表（恒假），属设计内语义；绑定对象中无主管 /
> 无管理员时规则整体不生效，用 `audit_data_permission_rules` 巡检可提示。

### 3.3 生效范围

- 全局 `DEFAULT_FILTER_BACKENDS` 自动对所有 ViewSet 生效；视图内用 `self.filter_queryset(self.get_queryset())` 显式触发。
- 导入（update 分支）、批量删除等写路径同样走 `filter_queryset`，因此**越权写同样被数据权限拦截**。
- **写侧载荷范围校验**（ADR-077）：创建 / 改归属的载荷（如用户的 `dept`、部门的 `parent`）与关系字段
  （`roles` / `rules`）同样按可见范围收敛——目标值必须落在 `get_filter_queryset` 可见面内，否则 400
  （超管与无请求上下文路径豁免）。
- 权限缓存与数据权限解耦：数据权限变更走 `DataPermission` 相关信号与角色失效链路。

### 3.4 部门级自治（部门管理员，ADR-077）

「部门管理员」= 被任命管理某部门及其全部下级的用户：任命事实源 `DeptInfo.managers`（through 记录任命人），
唯一写口为部门页的「部门管理员」动作（`POST /api/identity/dept/{pk}/assign-managers`，增量 `{add, remove}`）。

- **任命即装配**：预置内置角色「部门管理员」（用户管理子集 + 部门查看 + 字段白名单，`identity/builtin.py`
  幂等同步）与两条预置数据权限规则（`value.manager.user.ids` / `value.manager.dept.ids`，用户级绑定，
  规则挂用户而非角色——`UserRole` 无 rules 字段）一步就位；解任按「不再管理任何部门」回收角色与规则；
- **能力边界**：可管理本部门及下级的用户（列表 / 详情 / 新建 / 编辑，字段白名单内）；对象级与载荷级的
  写越界均被拒（404 / 400），不可见即不可写；建号/改号不涉及角色分配（可授权池收敛，越权挂角色 400）；
- **边界保证**：数据权限多授权并集取最宽（3.3 优先级口径）——管理员若被额外授予宽规则会突破部门边界，
  由 `sync_menu_permissions` 的 `[宽授权]` 段巡检暴露（只提示不阻断）；
- **注册边界**：`DeptInfo.managers` 字段未纳入字段树（`sync_model_field` 未同步），普通角色的部门列表
  里「部门管理员」列会被字段白名单裁剪（仅供查看，不影响任命与数据权限功能）。

## 四、第三层：字段权限

### 4.1 原理

```
FieldPermission(角色 × 菜单) ──> ModelLabelField 树（模型 → 字段）
BaseModelSerializer.__init__ 读取 request.fields，裁剪 serializer.fields
```

- `get_user_field_queryset(user, menu)`（缓存 10s）：汇总该用户在当前菜单下的字段授权，产出 `{模型名: {字段名集合}}`。
- `BaseModelSerializer` 在实例化时按 `request.fields` 保留/剔除字段，未配置字段权限的用户输出为空集（需显式授权）。
- 跳过条件：超管、白名单 URL、`PERMISSION_FIELD_ENABLED=False`（`ignore_field_permission`）。
- 单个字段可通过 `ignore_field_permission=True`（extra_kwargs）豁免，如文件回显字段。
- **异步任务链同源**：导入任务不经过视图 dispatch，`task_request.bind_view_task_context`
  在绑定视图上下文后复用 `IsAuthenticated.has_permission` 装载 `request.fields`
  （契约第 6 项）。缺少这一步时非超管的 serializer 字段白名单 fail-closed 为空集，
  逐行 `validated_data={}` 导致异步导入整体不可用；权限在任务执行期被收回时任务
  直接失败（fail-closed），不退化为静默空导入。

### 4.2 与 search-columns 的联动

元数据接口（search-columns）复用同一套字段权限，前端表单/表格列随用户权限动态收敛——**列隐藏是服务端裁剪的结果，不是前端遮挡
**。

### 4.3 数据集执行链路

数据集执行没有单一菜单上下文，字段权限按「角色在所有菜单下的该模型字段授权并集」生效，
并与数据集 `columns` 求交集。数据集通过 `field_permission_mode` 选择无配置时的语义：

- `inherit`（个人数据集、存量共享数据集默认）：角色没有任何该模型字段授权时不额外裁剪；
- `strict`（新建/切换为共享数据集默认）：无授权即空结果，`execute` / `aggregate` / `count_only`
  与请求级 `filters` 全部 fail-closed；
- 迁移前运行 `python manage.py dataset_field_permission_audit`，列出有共享数据集执行权限、
  但未配置字段权限的角色；`--fail` 可用于发布门禁。

**值级脱敏（DataMaskRule）同样在数据集执行链生效**：字段权限（能否看到该列）与值级
脱敏（看到的是否原文）是两层独立控制，`execute_dataset` / `aggregate_dataset` 在
ORM `values()` 直出后逐行套用 `common.core.mask.apply_output_mask`（与列表/搜索同源），
REST、定时报表 xlsx、仪表盘/大屏 WS 推送共用该内核，不存在「列表已掩码、数据集仍原文」
的旁路。聚合口径：

- 分组字段（含趋势桶）按字段规则掩码后再输出 `name` / `raw`（下钻过滤不能反推原值）；
- `sum` / `avg` 的取值字段若对当前浏览者命中掩码规则，直接 fail-closed——
  聚合结果无法掩码，继续执行等于泄露原值分布；`count` 不受影响；
- 豁免口径与序列化器一致（超管 / `?mask=false` + 更新权限 / 无规则）。

## 五、缓存与失效

### 5.1 缓存 key 一览（`packages/xadmin-common/common/decorators/cache.py`）

| 数据                  | key 形态                                        | TTL  |
|---------------------|-----------------------------------------------|------|
| API 权限映射            | `get_user_permission_{user_pk}_{method}`      | 24h  |
| 字段权限集合              | `get_user_field_queryset_{user_pk}_{menu_pk}` | 10s  |
| 响应缓存（list/retrieve） | `{视图}_{方法}_{user_pk}[_query_hash]`            | 视图配置 |

### 5.2 信号失效链路（`system/signal_handler.py`）

| 触发源                                                      | 信号                                              | 动作                   |
|----------------------------------------------------------|-------------------------------------------------|----------------------|
| Menu / SystemConfig / UserRole / DeptInfo / UserInfo 变更  | `post_save` / `pre_delete`                      | 批量失效相关用户权限缓存         |
| `UserRole.menu` / `UserInfo.roles` / `DeptInfo.roles` 变更 | `m2m_changed`                                   | 同上（覆盖 ORM 直改 M2M 场景） |
| 用户登出 / 显式踢出                                              | `user_logged_out` / `invalid_user_cache_signal` | 失效该用户权限缓存            |

变更**立即生效**（信号驱动），不依赖 TTL 过期；TTL 仅作兜底。

## 六、调试指引

### 6.1 常见问题排查

| 现象           | 排查步骤                                                                                                                          |
|--------------|-------------------------------------------------------------------------------------------------------------------------------|
| 403 但用户确有角色  | 1) 检查菜单 `is_active` 与 `menu_type`；2) 检查角色/部门挂载关系；3) 打印 `get_user_permission(user, method)` 结果；4) 确认 URL 匹配（精确 `$` 后缀 vs 前缀正则） |
| 改了角色/菜单权限未生效 | 确认请求经 API 发起（信号已挂 `m2m_changed`）；若绕过 ORM 直改，触发 `system.signal_handler.clean_cache_handler` 或调 `MagicCacheData.invalid_caches` |
| 列表有数据但详情 400/403 | 数据权限按「显式 action 覆盖 + list 缺省回退」：1) 若 detail 配了显式规则，detail 只认它，可能比 list 更窄（预期行为）；2) 若未配 detail，检查同资源 `list` 权限点是否绑定了授权；3) 显式规则只针对其它模型时视为未配置，仍回退 list |
| 字段输出比预期少     | 1) `request.fields` 是否被 FieldPermission 收敛；2) 字段所在模型是否在 `ModelLabelField` 树中正确挂载；3) 前端传参字段与后端字段名大小写                           |
| 列表空集但用户确有授权 | 1) 跑 `python manage.py audit_data_permission_rules`（非法规则 + 不生效提示：未绑定 / 主管部门规则无主管 / 引用对象已删除）；2) 数据权限页「试算」按目标用户实跑过滤，看实际生效授权与命中（排障口径见本节 FAQ；原教程已清理） |
| 导入/导出 403    | 未绑定模型的菜单回退到 list/create 权限——检查菜单的「绑定模型」配置                                                                                     |

### 6.2 Shell 验证片段

```python
from django.contrib.auth import get_user_model
from common.core.permission import get_user_permission, get_user_field_queryset

user = get_user_model().objects.get(username="xxx")
get_user_permission(user, "GET")  # 该用户全部 API 权限映射
get_user_field_queryset(user, menu)  # 指定菜单下字段权限
user.rules.all()  # 用户直接挂载的数据权限
[user_role.rules.all() for user_role in user.roles.all()]  # 经角色挂载的数据权限
```

### 6.3 日志关键字

- `get user permission failed`：权限查询异常（fail-closed 403）；
- `invalid_cache_data cache_key:magic_cache_data_get_user_permission`：权限缓存失效轨迹；
- `drf_exception`：权限类异常统一落点（含 requestId，可全链路追踪）。

## 七、测试地图

| 行为                       | 测试                                                                               |
|--------------------------|----------------------------------------------------------------------------------|
| 信号失效真实链路 + m2m_changed   | `tests/unit/system/test_signal_handler.py`                                       |
| API 权限 fail-closed / 白名单 | `tests/unit/common/test_core_permission.py`                                      |
| 数据权限 16 种规则过滤            | `tests/unit/common/test_data_permission_filter.py`、`test_dept_tree_cache.py`     |
| 字段权限裁剪                   | `tests/unit/common/test_serializer_field_permission.py`                          |
| 任务链字段权限契约（异步导入）          | `tests/unit/system/test_import_export_execution_context.py`                      |
| 数据集执行链值级脱敏              | `tests/integration/dataset/test_dataset_output_mask.py`                          |
| 副作用 GET 清单门禁              | `tests/unit/common/test_side_effect_get_actions.py`                              |
| 三层联动端到端（越权验证）            | `tests/integration/demo/test_book_viewset.py::TestBookDataPermissionIntegration` |

## 八、已知限制与设计取舍

- 菜单权限经角色控制后**不再**叠加数据权限过滤（避免双重配置，见 `get_user_menu_queryset` 注释）——给角色配数据权限即可，无需两处都配。
- `get_user_permission` 缓存 24h 是性能取舍，正确性由信号失效保证；若新增绕过 ORM 的写路径，必须补信号或手动失效。
- `request.fields` 由权限类 `IsAuthenticated._load_field_permission` 注入（非 DRF 中间件），非 DRF 权限类场景（WebSocket 等）需自行处理字段权限。
- **Cookie 承载 JWT 的残余风险面**：无感刷新与 WS/SSE 认证需要 Cookie 通道，SameSite=Lax
  已封住跨站 POST；剩余理论面是「名为 GET、实际改状态」的 action。全仓当前无此类端点，
  由 `tests/unit/common/test_side_effect_get_actions.py` 做 AST 清单门禁：新增副作用 GET
  必须改 POST，或在白名单登记「为何不能改 POST + Content-Type/自定义头收紧」的理由。
