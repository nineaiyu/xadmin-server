# ADR-077：部门级自治（三项补齐，功能立项）

- 日期：2026-09-30
- 状态：**已交付**（阶段一 + 阶段二；实现清单与验证见文末交付记录）
- 关联：ADR-031（多租户 no-go——本项为其登记的「部门级自治」替代路径）、ADR-056（数据权限配置与生效范围：多授权并集取最宽为显式设计）、ADR-006（数据权限编译器）；`common/core/data_scope/`、`common/core/filter.py`、`system/models/department.py`、`system/serializers/user.py`、`system/utils/modelset.py`
- 背景：多租户评估（ADR-031）结论为「一租户一实例、不引入租户维度」。走查确认：**部门级的数据/管理隔离已具备大半**（16 种数据权限规则、读侧与对象级写侧统一收敛），但距离「部门管理员能自主管理且硬保证出不了本部门」还差三项。本项只做这三项，不引入租户维度。

## 现状（事实基础）

**已具备**：

1. **数据权限规则面**：16 种规则类型（`system/models/field.py:15-31` 的 `KeyChoices`），部门相关族覆盖「本部门 / 本部门及下级 / 指定部门及下级 / 我主管部门及下级 / 我主管部门成员」；解析四段管线（validate→resolve→compile→combine）在 `common/core/data_scope/values.py:152-201`，leader 族解析实现可参照（`values.py:135-149`）。
2. **读侧收敛**：授权池 = 个人绑定 + 部门祖先链绑定（仅启用部门），无授权 fail-closed 空集（`common/core/filter.py:98-107` 授权池、`:117-169` 读侧消费）。
3. **对象级写侧收敛（框架统一）**：`get_object()` 走 `filter_queryset(get_queryset())`，按 pk 改/删范围外对象 → 404/400；批量删除/批量更新/导入 update 分支同样过滤（`common/core/modelset/batch.py:81,242`）。
4. **授权池约束先例**：empower 的 roles/rules 过 `get_filter_queryset`（`system/utils/modelset.py:77-87`），部门序列化器对关系池同口径（`system/serializers/department.py:92-104`）。
5. **部门侧既有机制**：`DeptInfo.leader`（主管，数据权限解析上下文，`system/models/department.py:33-41`）与 `DeptInfo.roles/rules`（部门绑角色/规则，成员继承，`department.py:42-43`）。

**缺口（三项补齐的对象）**：

1. **没有「部门管理员」实体**：`DeptInfo.leader` 只承载审批与数据权限解析语义，没有「任命即获得本部门（及下级）管理权」的闭环——现状只能人工拼「全局权限点角色 + 数据权限规则」两件套，任命变更不联动授权；
2. **写侧载荷未校验**：创建用户/子部门时可指定范围外的 `dept`/`parent`、可把对象改出范围；`UserSerializer` 的 `roles`/`rules` 关系写入不过可授权池（`system/serializers/user.py` 无过滤，仅 empower 与部门序列化器有）；
3. **边界保证不成体系**：数据权限多授权并集取最宽（ADR-056 显式设计，无「上限」表达），权限点为全局粒度（`destroy:SystemUser` 授予即可作用于任意对象）——「部门管理员出不了本部门」目前靠配置纪律，无装配收敛与巡检可见性。

## 决策（三项补齐）

### D1 部门管理员实体与任命装配

- `DeptInfo` 新增 `managers`（M2M → UserInfo，through 模型记录任命人/时间），作为「谁管这个部门」的唯一事实源；与 `leader` 职责分离（leader 保审批语义，managers 承载管理权，可多人、可不同于主管）。
- 数据权限新增规则族（模照 leader 族实现）：`value.manager.dept.ids`（我管理的部门及下级）、`value.manager.user.ids`（我管理部门的成员）；单层版（不含下级）按需再加。
- 任命端点 `POST /api/system/dept/{pk}/assign-managers`（权限点 `assignManagers:DeptInfo`）：一步完成「设 managers + 维护预置『部门管理员』角色成员」（幂等、原子、审计），解任时若用户不再管理任何部门则回收角色成员。
- 种子下发预置角色「部门管理员」：用户管理子集权限点（查看/新建/编辑/停用，不含删除与密码重置等高危动作）+ 数据权限规则绑定（manager 族）；角色成员由任命端点维护（人工改成员为越权操作，登记边界）。

### D2 写侧载荷范围校验（框架级）

- 对象级已有（D 现状 3），本项补**载荷级**：序列化器声明「归属字段」（首期 `UserInfo.dept`、`DeptInfo.parent`，声明式扩展），非超管写入时校验新值必须在**数据权限可见范围**内（复用 `get_filter_queryset`，与读侧同源）；
- 关系字段赋值面（`roles`/`rules` 等）统一过可授权池（对齐 empower 口径，补齐 `UserSerializer` 缺口）；
- 首期接线 User / Dept，其余模型随声明式框架按需接线（成本低）；
- **行为变更登记**：非超管创建/改归属受范围约束（超管不变）；存量部署中「非超管管理端账号」的可用归属面收敛为其数据权限范围。

### D3 边界保证与巡检（不引入上限语义）

- **不引入「数据可见上限（deny ceiling）」新语义**：编译器组合逻辑为核心路径，扩张成本高；且当前无「多级管理员递归授权 + 硬上限」真实场景；
- 边界保证改用三层既有 + 一层新增：
  a. 装配收敛：D1 的任命端点唯一维护管理员授权来源（可识别、可回收）；
  b. 取值域约束：empower/部门序列化器的既有过滤（D 现状 4）保证管理员不能给他人挂超出自己范围的角色/规则；
  c. **巡检告警（新增）**：权限巡检扩展「部门管理员持宽授权」检查项（持有 `value.all` 等宽规则/宽角色 → 输出缺口清单），缺口清零进回归测试；
  d. 残余风险显式登记：超管手工为管理员挂宽规则会突破部门边界——属配置行为，由巡检可见。
- 「数据可见上限（分层授权 ceiling）」登记为评估出口：出现「多级管理员 + 硬上限」诉求时按预案评估（编译器引入 ceiling 组合）。

## 备选与不选

| 方案 | 不选原因 |
|---|---|
| 复用 `DeptInfo.leader` 承载管理权 | leader 有审批链语义（主管审批、字段级审批人解析），管理权可与主管不同人且需多人；语义混用会让审批解析随管理任命漂移 |
| 用「部门绑角色/规则」（`dept.roles/rules`）做管理员装配 | 其语义是「该部门全体成员共享的角色」，任命是「指定个人」，会把权限面扩大到全体成员 |
| 引入「数据可见上限（deny ceiling）」 | 编译器核心组合逻辑扩张、成本高、无真实场景；先用装配收敛 + 取值域约束 + 巡检覆盖（见 D3） |
| 每类管理动作拆独立「范围内」权限点 | 权限点面膨胀且与既有全局点重复；D2 的范围校验使既有权限点天然获得「范围内」语义 |
| 引入租户维度（tenant_id） | ADR-031 已 no-go；部门级自治不需要租户列 |

## 实施拆分

| 阶段 | 范围 |
|---|---|
| 阶段一（核心闭环） | D1 全部（managers + 规则族 + 任命端点 + 预置角色 + 前端部门页任命）+ D2 的 User/Dept 接线 + D3 的巡检检查项 |
| 阶段二（按需） | D2 其余模型接线；单层 manager 规则；管理视图（「我的管辖」聚合页）；文档与教程章节 |

## 验收

- 单测：manager 规则解析（含下级展开 / 无管理职责返回空 / 停用部门剔除）、任命端点幂等与回收、D2 归属校验（范围内通过 / 范围外拒绝 / 超管旁路）、巡检检查项命中与清零；
- 集成：任命 → 管理员增改本部门（及下级）用户成功；越界四类被拒（改外部门用户 404 / 创建指定外部门 400 / 改归属出范围 400 / 挂超范围角色拒绝）；解任后能力回收；
- e2e：部门页任命流程 + 管理员登录操作闭环（双浏览器）；
- 门禁：按项目惯例八件套 + 前端 typecheck / lint / vitest / 契约 / i18n / 包体；
- 部署：migrate（managers through 表）、`load_init_json`（预置角色 + 权限点 + 元数据）、`compilemessages`（词条）。

## 边界（登记）

- 部门级自治是**单租户内**的范围收敛，不提供租户隔离（ADR-031 结论不变）；将来若 SaaS 化，本项成果可复用（部门=租户时 managers 即租户管理员）；
- 「及下级」为默认管辖语义，单层（不含下级）按需加规则类型；
- 预置角色成员由任命端点维护，手工调整不保证一致（巡检可见）；
- 数据权限多授权并集取最宽的既有语义不变（ADR-056），超出部门边界的授权后果由巡检暴露。

## 交付记录（2026-10-01）

**实现清单**（阶段一 + 阶段二全部落地）：

| 面 | 落点 |
|---|---|
| 实体 | `DeptInfo.managers`（through `DeptManagerAssignment`：dept / user / created_by / created_time，唯一约束）；迁移 system `0009_dept_managers` |
| 规则族 | `value.manager.dept.ids` / `value.manager.user.ids`（KeyChoices / `rule_meta` / `RUNTIME_VALUE_TYPES` / `resolve_rule` 解析 / 写侧 match 白名单 / 预览解码 / 数据权限巡检命令全覆盖） |
| 任命装配 | `system/utils/dept_managers.py`：端点一步维护 through 行 + 预置角色成员 + 用户级预置规则；解任按「不再管理任何部门」回收；幂等 |
| 端点 | `POST /api/system/dept/{pk}/assign-managers`（权限点 `assignManagers:SystemDept`）+ `GET .../user-options`（候选，list 同口径）+ `GET .../managed`（我的管辖） |
| 预置角色 | 内置角色 `DeptManager`（`system/builtin.py` 幂等同步：权限点清单 + **字段白名单**（`_ensure_role_fields`，缺失补建为模型全字段）） |
| 写侧校验 | `assert_within_data_scope`（`common/core/filter.py`）+ `UserSerializer`（dept / roles / rules）+ `DeptSerializer`（parent） |
| 巡检 | `audit_wide_manager_grants`（部门管理员持宽规则）+ `sync_menu_permissions` 的 `[宽授权]` 段 + 数据权限巡检命令的「管理部门」类告警 |
| 前端 | 部门页「部门管理员」动作（ReDialog + `DeptManagersDialog.vue`，增量载荷）；「我的管辖」页（`/system/my-scope/index`，统计 + 部门卡片 + 成员跳转）；规则元数据镜像（constants / ruleMeta / presets） |
| 种子 | `menu.json` / `menumeta.json` 补：`assignManagers:SystemDept` 权限点与「我的管辖」页面菜单（pk 与运行库一致） |

**与立项方案的偏差（实现期修正）**：

1. **预置角色的数据权限规则改为用户级绑定**：`UserRole` 无 `rules` 字段（数据权限只挂用户 / 部门），任命装配
   改为把两条预置规则绑到被任命用户（解任回收）——角色只承载权限点与字段白名单；
2. **`managed` / `user-options` 不新增独立权限点**：`GET /dept/managed` 由 `api/system/dept/(?P<pk>[^/.]+)$`
   （`retrieve:SystemDept`）路径正则命中；`user-options` 走框架 shared_list 注册表（权限与 list 同口径）；
3. **新增内置角色字段白名单同步**（`_ensure_role_fields`）：字段权限 fail-closed（无白名单 = 读写字段被整体
   裁剪），部门管理员开箱可用需要基础白名单；同步为「模型全字段、缺失时补建」，管理员在角色页细化后不回写；
4. **新增模型字段未进字段树**（登记边界）：`DeptInfo.managers` 未经 `sync_model_field` 纳入标签树，普通角色
   的部门列表「部门管理员」列会被字段白名单裁剪（仅供查看，不影响任命与数据权限功能）。

**验证证据**：

- 后端全量 `pytest` exit 0（本批新增：单测 12 + 集成 12）；`ruff check/format`、行数 / 跨 app / 缓存键 /
  `makemigrations --check`、文档四件套（事实 / 索引 / 路径 / 站点导航 / 教程镜像）全绿；
- 前端 `typecheck`（tsc + vue-tsc）/ eslint / prettier / stylelint / `check:i18n`（zh 3396 = en 3396）/
  契约三件套 / `as unknown as` 基线 / 版本 / `vitest` **717** 全绿；`pnpm build` + 包体分账
  （代码 +6.9/15KB、i18n +11/15KB 预算内）；
- E2E `dept-managers.e2e.ts` **双浏览器 4 passed**（任命闭环：清理 → 打开弹窗 → 远程搜索选中 → 保存 →
  列展示 → 复开确认 → 回收；我的管辖页：任命 → 统计与部门卡片 → 成员入口 → 回收）；
- 顺带修复：`tests/integration/dataset/test_report_cron.py` 的月报到期断言与真实时钟耦合（每月 1 日
  00:00~08:00 窗口内取到「上月到期点」而假失败，改为显式判定时刻）。

**E2E 教训（已回填）**：`force: true` 的点击会按坐标派发到覆盖层（残留 popper），不触发目标按钮——
「点击成功但回调未执行」类问题先排除 force 分支；EP select 选中后下拉保持打开，收起姿势为点弹窗标题区，
保存按钮用普通点击（actionability 重试等待落点）。

**部署注意**：① 需 `migrate`（system `0009_dept_managers`）；② 需 `load_init_json`（`assignManagers:SystemDept`
权限点 +「我的管辖」页面菜单）；③ 需 `compilemessages`（新增词条）；④ 内置角色「部门管理员」与字段白名单
由 `post_migrate` 自动同步（存量库升级即生效）；⑤ 「部门管理员持宽授权」巡检随 `sync_menu_permissions` 执行。
