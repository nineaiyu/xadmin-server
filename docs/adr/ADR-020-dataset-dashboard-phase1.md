# ADR-020：数据集 + 仪表盘一期（可视化）

- 状态：已接受
- 日期：2026-09-13
- 关联：年度开发计划 2026.10-2027.09 §四 W5（G3a）；ADR-015（重依赖红线）；
  数据权限编译器（common/core/data_scope.py，16 种规则）；G8 报表轻量版（2027-02
  窗口，复用本 ADR 的数据集）；G4b AI 查数（2027-05，NL→数据集 DSL 的地基）

## 背景

对标项目（jeecg 仪表盘设计器 / 积木报表）的可视化能力是 xadmin 缺口：现有统计
只有固定的监控面板与 welcome 首页（硬编码端点）。目标（G3a 一期）：**数据集**
（绑定现有模型的受控查询，执行时按调用者数据权限过滤）+ **仪表盘**
（布局存储 + 图表卡片）。

大件风险（计划 §五）：本 ADR 按「先评估出口」纪律给出可降级/可砍路径。

## 决策

### 1. 数据集 = 模型白名单内的受控查询定义（禁原生 SQL）

`Dataset` 模型（system app）：`name`(唯一) / `description` / `bound_model`
（`label_lower`，白名单 = ModelLabelField DATA 根节点，与数据权限编辑器同源）/
`columns`(JSON 字段名列表，白名单 = 该模型 DATA 子节点) / `filters`(JSON：
`[{field, op, value}]`，op 白名单 exact/in/gte/gt/lte/lt/contains/startswith/
isnull) / `ordering`(字段 ± 前缀) / `row_limit`(默认 1000，硬上限 5000) /
`visibility`(personal / shared) / `config`(JSON，一期占位：仅允许
`{"date_field": <字段名>}` 供趋势聚合)。

- **禁原生 SQL**：查询只由 ORM 表达（`apps.get_model` + `values(*columns)`），
  字段/模型越出白名单在保存与执行双侧校验拒绝；
- 行级过滤复用**既有入口** `get_filter_queryset(queryset, request.user)`
  （common/core/filter.py:28）——三值布尔代数、部门祖先链 + 个人授权同池、
  坏规则 DENY_ALL、无授权 `none()` 的 fail-closed 语义全部继承，不自写规则解析；
- 聚合动作 `aggregate`：入参 `group_by`(白名单字段) + `metric`(count，或
  sum/avg 限数值字段) + `date_trunc`(day/month，仅 DateTime 字段)，输出
  `{name, value}` 序列（上限 365 桶）——图表卡片的数据源；
- 与数据权限的菜单语义（登记边界）：数据集执行接口自身的权限菜单会成为
  `user.menu` 上下文，**绑定到其它菜单的数据权限规则在该上下文不生效**——
  数据集场景依赖「未绑菜单（全局）授权」；数据集配置页提示该口径。

### 2. 仪表盘 = 布局 JSON + 卡片引用数据集，两档可见性

`Dashboard` 模型：`name` / `layout`(JSON：`[{id, dataset, title, chart_type,
group_by, metric, date_trunc, span, sort}]`，卡片内嵌不建子表——一期卡片就是
"数据集 + 聚合参数 + 图形类型"的三元组，无独立状态) / `visibility`(personal /
shared)。

- **可见性两档**：`personal` 仅创建者可见；`shared` 所有登录用户可见（只读）。
  列表过滤 `Q(creator=user) | Q(visibility=shared)`；更新/删除仅创建者与超管
  （对象级校验在 serializer），共享方不能改布局；
- 卡片执行走数据集同一管线：浏览仪表盘时前端按卡片逐个调 `aggregate`/`execute`，
  权限随**浏览者**过滤（同一仪表盘不同人看到不同数字，天然多租户安全）；
- 图表类型一期四种：`number`（计数卡，execute count）/ `line`（date_trunc 趋势）/
  `bar` / `pie`（group_by 分布）。

### 3. 前端零新依赖

- ECharts 已有懒加载封装（plugins/echarts.ts），仅追加注册
  BarChart/PieChart/LineChart 所需组件；页面沿用 `loadEcharts() → echartsReady →
  v-if → useECharts(ref, {renderer:"svg"})` 链路与 TrendChart 的 waitSized 防 0
  尺寸写法；
- 布局用 Tailwind/CSS grid + 既有 sortablejs 拖拽排序，**不引入**
  vue-grid-layout/gridstack（ADR-015 红线）；卡片宽度以 span 档位（3/6/9/12）
  配置化，不做自由拖拽缩放（登记 G3b 评估）；
- 页面：`views/dashboard/index.vue`（仪表盘）+ `views/dashboard/dataset/index.vue`
  （数据集管理，列表 + PlusForm 弹窗配置模型/字段/过滤）；菜单种子走
  「数据分析」目录，权限点按动作注册。

### 4. 评估出口（大件风险条款，可降级/可砍）

| 层 | 出口条件 | 降级路径 |
|----|----------|----------|
| 聚合动作 | 一期只需 count/sum/avg + date_trunc | 若需求膨胀（多指标/环比），先砍为纯 count |
| 卡片编辑 | 弹窗表单即可满足 | 不做画布式编辑器（重依赖红线） |
| 布局 | span 档位 + 排序 | 若拖拽体验不达标，砍为纯排序（sortablejs 已是最小依赖） |
| 整体 | 若数据集执行性能/权限口径评审不通过 | 仅砍仪表盘，保留数据集 API（G4b AI 查数仍需其地基） |

## 后果

- **明示不做**（一期边界，转候选池需评审）：跨模型 join 数据集、SQL 表达式、
  定时报表邮件（G8/2027-02）、卡片级权限（跟随数据集权限）、图表联动/下钻、
  大屏投屏模板（G3b）、字段权限在数据集列上的叠加（列白名单由数据集配置承担）；
- **安全**：模型/字段/op 三层白名单 + 保存执行双侧校验；行级 fail-closed 继承
  数据权限编译器；共享仪表盘只读；`PERMISSION_DATA_ENABLED=False`（全局关闭
  数据权限）时数据集跟随系统语义不过滤——ADR-020 登记该口径，部署侧开启
  数据权限是数据集安全的前提；
- **性能**：row_limit 硬上限、聚合桶上限、执行结果不缓存（一期读频低，
  G3b 再评估缓存）。

## 测试与验收

- 单元：白名单校验（越权模型/字段/op 拒绝）、filters→ORM 编译、aggregate 桶
  上限、ordering 注入安全；
- 集成：fail-closed（无授权用户执行数据集 → 空结果；有 value.user.id 规则 →
  只见本人数据）、共享语义（personal 不可见 / shared 只读可见、非创建者改布局
  被拒）、越权（匿名/无菜单权限 401/403）；
- E2E 主链路：建数据集 → 建仪表盘 → 加卡片 → 图表渲染 → 共享可见；
- 门禁：pytest / ruff / i18n po；前端 typecheck / eslint / locale-keys；
  全量 E2E 回归。
