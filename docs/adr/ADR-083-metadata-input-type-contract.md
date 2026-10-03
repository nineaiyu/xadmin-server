# ADR-083：元数据 input_type 词表冻结为稳定公共契约（多端渲染前置）

> **日期**：2026-10-03
> **状态**：已交付
> **关联**：[ADR-079](ADR-079-contract-seam-interface.md)（契约治理同族）；`docs/schema/` 元数据协议（T2.3，载荷结构契约既有门禁 `test_metadata_schema.py`）；[ADR-043](ADR-043-remote-suggestions.md)（api-* 远程联想族）
> **代码路径**：`common/core/modelset/input_types.py`（词表真源）；`docs/schema/search-{columns,fields}.schema.json`（枚举落盘）；`tests/unit/common/test_metadata_schema.py`（闭包 + 锁步守护）；client `src/components/RePlusPage/__tests__/metadata-vocabulary.spec.ts`（跨栈覆盖对账）
> **背景**：载荷结构契约已由 JSON Schema 门禁固化，但 `input_type` 仅 `type: string`——后端可随时下发前端不认识的新类型（注册表静默回退），前端渲染器键与后端词表也无对账。多端渲染（同一份元数据 × 多套渲染器，如 H5）要求词表本身成为显式、受守护的公共契约，故先行冻结。

## 决策

- **D1：词表真源（封闭核心 + 开放族 + 回退登记三段式）**。`DECLARED_INPUT_TYPES`（32 类型 frozenset）= 平台元数据端点可下发的封闭核心：DRF `label_lookup` 实际可达面 + 自定义字段声明面（labeled_choice 族 / json / phone / color）+ 关联族 `_file`/`_image` 组合 + `textarea` 覆写 + search 侧 widget 覆写与合成（select-multiple / datetimerange / select-ordering）+ filter widget 默认面（text/number/select）+ 业务显式声明且走回退透传的 `input`。开放族 `INPUT_TYPE_PREFIX_FAMILIES = ("api-",)`（apiSearch 注册组件 + suggest_url 联想，业务可扩展，不做封闭枚举）。`FALLBACK_RENDERED_INPUT_TYPES = {email, input}` = 无内置渲染器、依赖注册表回退语义呈现的登记类型（search/form 回退渲染器透传 valueType，detail 不配置即默认文本）。DRF 长尾类型（decimal/url/time/duration/regex/slug/nested object）未实际下发、前端无渲染器，**不预登记**——出现即被闭包测试拦截，按扩展流程有意识处置。
- **D2：Schema 枚举 + type 收敛**。两份元数据 Schema 的 `input_type` 改为 `anyOf: [封闭枚举, string + ^api- pattern]`，随既有镜像工作流（sync:contract → 生成 TS 类型）流转。pattern 分支显式 `type: string`：生成 TS 类型收敛为 `string`——框架边界对业务自定义类型（api-* / 二开注册渲染器）保持开放，**穷尽性对账不进类型系统、由守护测试承担**（枚举进 TS 联合会卡死 `PageColumn._column` 对 api-* 的合法构造）。`x-fallback-rendered` 为 Schema 自定义关键字（校验器忽略，仅供跨栈对账读取）。
- **D3：三面守护，双向拦截**。① **载荷闭包**（server pytest，真实端点 × Book/User 矩阵）：下发 input_type ⊆ 词表 ∪ api-*——平台侧新增可下发类型未登记即 fail；② **Schema 锁步**（server pytest）：两份 Schema 的枚举 / pattern / 回退登记与真源逐一相等，单向漂移双向拦截；③ **跨栈覆盖**（client vitest，原生 import 三张注册表与镜像 Schema）：词表非回退类型必有内置渲染器、回退类型必无内置渲染器、注册表键必在词表内——前端不得发明后端未登记的类型。client-only CI 无法跨仓时，服务端两面仍完整生效。
- **D4：扩展流程（登记制）**。新 input_type = 词表登记（连同呈现归宿：内置渲染器 / 回退）→ 同步两份 Schema 枚举 → client `pnpm sync:contract` + 补注册表/守护。三面守护保证任何一步遗漏在 CI 可见。

## 验收

1. `test_metadata_schema.py` 全绿（11 例：既有 Schema 校验 + 载荷闭包 + 锁步）；client typecheck / vitest（词表对账 3 例）/ check:contract / check:contract-usage 绿；
2. 实证对账通过：五视图集（demo/book、system/user/role/dict、dataset/dform）真实载荷 29 个 observed 类型 + 前端三张注册表 31 键全部落在词表内；`email`（DRF EmailField）/`input`（demo.Book managers filter 显式声明）两类无内置渲染器，以回退语义显式登记；
3. 生成 TS 类型 `input_type: string`（开放边界），`columnRules.spec` 等既有用例零改动。

## 边界（登记）

- **契约面**：本 ADR 只冻结 `input_type` 词表；载荷结构由既有 Schema 门禁（`additionalProperties: false`）守护。**页面级布局与交互（按钮组 / 自定义弹窗 / 分栏）不在元数据契约内**，多端渲染若需布局描述属新契约，另行立项。
- **不动**：dform 动态表单的控件词表（`dform_constants.ALLOWED_TYPES`）是另一套独立契约，本次不合并（两词表归一留多端立项时评估）；`choices` 截断 / suggest 降级语义不变。
- **不做**：把 `input_type` 枚举收进 TS 联合类型（D2，开放边界优先）；运行期对未登记类型的 fail-fast（业务扩展面要求运行期宽容，登记制靠测试拦截）。

## 交付记录

- 2026-10-03：词表真源 + Schema 枚举 + 三面守护落地；多端渲染（H5 renderer）立项触发条件另行登记（NEXT-DEV-PLAN §一 O12）。
