# 组件手册（二次开发）

> 定位：回答"框架里**有哪些组件**、每个组件**怎么用 / 怎么配 / 往哪扩**"。
> 与相邻文档的分工：
>
> | 我想…… | 读这篇 |
> |---|---|
> | 第一次动手建一个模块 | [guide/first-module-30min.md](../guide/first-module-30min.md) |
> | 按任务找步骤（加字段 / 加按钮 / 加任务……） | [guide/recipes.md](../guide/recipes.md) |
> | 在两个方案之间做选择 | [方案选型与对比.md](方案选型与对比.md) |
> | 理解某个机制的设计与边界 | [overview.md](overview.md) → 各机制篇章 |
> | 改内核前确认边界 | [packages/xadmin-common/common/README.md](../../packages/xadmin-common/common/README.md) |
>
> 本文每个组件都标注**权威源**（类型/实现的唯一事实处）——文档与代码冲突时以权威源为准，
> 改组件时先改权威源、再回来同步本页。

> **本文为总册**：全景图（§〇）、工程化设施（§三）、扩展点速查（§四）、相关文档（§五）见下；
> 后端 14 组组件见 [handbook-backend.md](handbook-backend.md)，前端 10 组组件见 [handbook-frontend.md](handbook-frontend.md)。

## 〇、全景图

```
后端（xadmin-server）                          前端（xadmin-client）
┌──────────────────────────────┐              ┌──────────────────────────────┐
│ 业务 app    models / serializers / views /  │  页面层    views/**（RePlusPage 声明式页面）
│             services / tasks / modules.py   │           └ utils/hook.tsx 逻辑收敛层
├──────────────────────────────┤              ├──────────────────────────────┤
│ 工程层      server/（settings 拼装 / urls / │  组件层    RePlusPage / ReDialog / ReDrawer /
│             asgi / celery / middleware）    │           ReIcon / RePlusSearch / ReAuth …
├──────────────────────────────┤              ├──────────────────────────────┤
│ 内核层      common/（模型基类 / modelset /  │  基础设施  api（BaseApi）/ utils/http /
│             元数据 / 权限 / 缓存 / 任务 /    │           router / store / directives / i18n
│             配置 / 模块裁剪 / SDK）          │
└──────────────────────────────┘              └──────────────────────────────┘
         │                                                 │
         └──────────── 咬合点（§四）──────────────────────┘
   元数据（search-columns / search-fields）· 权限码（动作:组件名）· 契约（docs/schema ↔ contract/schema）
```


## 三、工程化设施

| 设施 | 说明 |
|---|---|
| 契约与类型 | 服务端 `docs/schema/`（真源）→ 前端 `contract/schema`（`pnpm sync:contract` 一键镜像 + `gen:metadata-types` 生成类型）；CI `check:contract` 防绕过 |
| 版本一致性 | `pnpm check:version`（tag ↔ `server/const.py` ↔ client `package.json`）；`doctor` 同源自检 |
| 服务端门禁 | pytest（2800+，sqlite+FakeRedis 零外部依赖）/ ruff / 跨 app import / 文件行数 500 / 缓存键 / makemigrations / 文档事实（`check_doc_facts.py`） |
| 前端门禁 | `typecheck`（strict 全仓单轨零错误，2026-09-26 起双轨合一）/ eslint（`no-explicit-any` error）/ prettier / stylelint / vitest / 文件行数 500 / bundle-size（+15KB 预算）/ 契约 |
| E2E | Playwright 双浏览器（chromium+webkit）+ 专项（smoke / visual / perf / a11y / csp）；纪律见 `xadmin-client/e2e/README.md`（**改后端必须 `test:e2e:fresh`**） |
| 覆盖率 | 服务端 CI 门禁 `--cov-fail-under=85`（`.github/workflows/test.yml`）；前端 vitest 覆盖率阈值（含 registry / renders 等关键文件） |


## 四、依赖关系与扩展点速查

### 4.1 "要做什么 → 用哪个组件"

| 我要…… | 用 |
|---|---|
| 出一个标准列表页 | `BaseModelSet` + `BaseModelSerializer` + `BaseFilterSet` + `<RePlusPage>` |
| 加一个按钮触发接口 | 后端 `@action` + 权限点；前端 `BaseApi` 子类方法 + `operationButtonsProps.buttons` |
| 换单元格/表单控件 | `*Format` 出口（页面级）或渲染器注册（全局级）；先按 §六「表单选型判定」确认是否该写表单 |
| 弹一个表单/详情 | `addDialog` / `addDrawer` / `openDialogDrawer` |
| 选一个用户/部门/角色 | `api-search-user` 等（表单）或 `RePlusSearch`（独立选择器）或联想（大表搜索） |
| 大表关联字段搜索 | `PkMultipleFilter` + `SuggestionsAction` + 前端自动 `SuggestSelect` |
| 定时跑一件事 | `@register_as_period_task(interval=..., module=...)` |
| 发一条多通道通知 | 消息类实例 `.publish(is_async=True)`（新渠道加 `backends/<name>.py`） |
| 让业务走审批 | `approval_flow.engine.create_instance(biz_type=..., biz_id=...)` + 终态信号 |
| 对外投递事件 | `task/services/webhook/delivery.py::emit_webhook_event`（事件先登记 `EVENT_CATALOG`） |
| 枚举文案可运营 | `DictChoiceField` + 字典页维护 |
| 让功能可裁剪 | `{app}/modules.py`（`generate_module`）+ `config.yml` 的 `MODULE_*` |
| 升级后收尾 | `manage.py post_upgrade` / `doctor` |

### 4.2 扩展点总表

| 扩展点 | 位置 | 时机/约束 | 参考实现 |
|---|---|---|---|
| 新 `input_type` 渲染 | 后端 `drf/metadata.py::get_field_type`（isinstance 分支）+ 前端 `RePlusPage/src/utils/registry.ts` 三注册函数 | 前端注册必须早于页面首渲染 | `renderers-*.tsx`；配对守护 `renderers-pairing.spec.ts` |
| `api-search-*` 组件 | `registerApiSearchComponents` | 同上 | `src/views/system/apiSearch.ts` |
| 联想 fetcher | `registerSuggestFetcher` | 同上 | 同上 |
| 列/表单覆盖 | `*Format` props（页面级） | 页面内 | `src/views/system/user/utils/useUserColumnFormats.tsx` |
| 弹窗表单复用 | `openDialogDrawer` | 页面级 | `useUserColumnFormats.tsx::handleRoleRules` |
| 内置图标集扩展 | `iconRegistry.ts::SET_LOADERS` / `offlineIcon.ts` | 离线约束：不得回退在线 | 双形态注册 |
| 全局 `el-*` 组件 | `src/plugins/elementPlus.ts` | 有单测比对清单 | — |
| 值级加密 | `packages/xadmin-common/common/base/crypto.py::signer` | 敏感字段入库前 | webhook secret / AI api_key |
| 新增缓存类 | `packages/xadmin-common/common/cache/storage.py::RedisCacheBase` | 键名过 `check_cache_keys.py` | — |
| 周期任务 | `@register_as_period_task(module=...)` | module 归属可裁剪 | `system/tasks/` |
| 通知渠道 | `notifications/backends/<name>.py`（模块级 `backend`） | 渠道枚举补 `BACKEND` | `notifications/backends/email.py` |
| 通知消息类型 | `@register_message` + `register_backend_msg` | 渲染映射补齐各渠道 | `notifications/notifications.py` |
| Webhook 事件 | `EVENT_CATALOG` 登记 + `emit_webhook_event` | 事件契约守护测试 | `task/services/webhook/` |
| 审批业务绑定 | `create_instance(biz_type, biz_id)` + 监听 `approval_instance_finished` | 终态信号在 `system/signal.py` | 请假业务 `approval/utils/leave.py` |
| 可裁剪模块 | `{app}/modules.py`（`ModuleSpec`） | `generate_module` 生成 | `packages/xadmin-common/common/core/modules/registry.py` |
| 配置键 | 部署期 `config_example.yml`+`defaults.py`；运行期 `system_conf.py`+种子 | 两处同名；种子守护测试 | — |
| 中间件 | `MIDDLEWARE` 插入 | 开关用 `MiddlewareNotUsed` | `server/middleware.py` |
| 自定义渲染器（SSE 等） | `packages/xadmin-common/common/drf/renders/` + ViewSet `get_renderers()` | **ViewSet 必须覆写 `get_renderers`**（装饰器只对 `@api_view` 生效） | `message/views.py::ChatAiViewSet` |
| 数据源非 ORM 的 ViewSet | 自带 `batch_destroy` | 不能依赖 QuerySet 能力 | `SecurityBlockIpViewSet` |

### 4.3 前后端咬合点（改一侧必看另一侧）

| 咬合点 | 约定 | 破坏后果 | 防护 |
|---|---|---|---|
| 元数据协议 | `search-columns` / `search-fields` JSON Schema | 表格空列 / 表单缺域（**静默**） | 契约镜像 + 守护测试 + DEV 警示条 |
| 权限码 | `{action}:{ViewSetName}` ↔ 组件 `name` | 页面/按钮不渲染（**静默**） | 种子守护测试 + `doctor` |
| 统一响应 | `code=1000` 成功壳 | 页面无提示/误提示 | 契约 + 错误策略表 |
| FormData v1 | 点分键序列化（`AxiosMultiPartParser` 反向还原） | 文件/嵌套字段提交失败 | 协议守护测试 |
| 路由生成 | 菜单 `component` 字符串 ↔ `src/views/**` 路径 | 空白路由 | DEV 报错 |
| 契约变更流程 | 先改 `docs/schema` → `pnpm sync:contract` → 提交生成类型 | CI 红 | `check:contract` |


## 五、相关文档

| 文档 | 内容 |
|---|---|
| [framework-cookbook.md](framework-cookbook.md) | ViewSet 选型、Action↔BaseApi 对照、覆写红线 |
| [metadata-protocol.md](metadata-protocol.md) | 元数据协议规范 |
| [方案选型与对比.md](方案选型与对比.md) | 组件/方案的特点、适用场景与对比 |
| [../guide/recipes.md](../guide/recipes.md) | 典型扩展流程处方集（按任务索引） |
| [模块化与功能裁剪.md](模块化与功能裁剪.md) | 模块清单、裁剪矩阵、CLI |
| [../../packages/xadmin-common/common/README.md](../../packages/xadmin-common/common/README.md) | 内核目录地图与边界规则 |

## 六、表单选型判定（元数据表单 vs 手写表单）

新增/改造表单前先在这里选型——**两套契约并存，选错会重复生产「后端有元数据、前端却手写整套
字段」的页面**。判定顺序自上而下，命中即停：

| 形态 | 选型 | 落点 |
|---|---|---|
| 标准模型 CRUD：后端 `BaseModelSet` + 序列化器字段可被元数据 21 键表达 | **元数据表单** | `auth.create/partialUpdate` 保持开启；页面只写 `addOrEditOptions` |
| 有模型，但含元数据表达不了的结构（嵌套子表 / 三元组行编辑器 / 逗号分隔数组 / 独立端点读写） | **混合**：基础字段走元数据，复杂字段作为扩展字段注入或保留为页面专属弹层 | `addOrEditOptions.props.columns` + `renderField` / `beforeSubmit` / `apiReq` |
| 无模型元数据（登录、认证动作、内存草稿、预览试算、schema 不同源） | **手写 `XxxForm.vue`** | 必须登记豁免（见本节末） |

### 6.1 标准模式（元数据表单）

```ts
const addOrEditOptions = shallowRef<RePlusPageProps["addOrEditOptions"]>({
  props: {
    columns: {
      // 就地微调；返回 undefined = 该列不参与表单
      dept: ({ column }) => { column.valueType = "cascader"; return column; },
      rank: ({ column }) => { column.fieldProps = { ...column.fieldProps, min: 1 }; return column; }
    },
    formProps: { rules: ({ rawFormProps }) => ({ ...rawFormProps.rules, /* 覆写/追加 */ }) },
    beforeSubmit: ({ formData, formOptions }) => { /* 数组↔文本、空串→null… */ return formData; }
  }
});
```

要点：
- 字段本体、校验必填、`required`、`max_length`、`read_only` 过滤全部由后端元数据决定；
- `auth.create/update/partialUpdate` **不再关闭**（关闭后框架默认新增/编辑按钮消失，只能手写弹窗）；
- 只读字段（`SerializerMethodField`、`read_only=True`）不会进入表单列；确需展示时按 6.2 注入扩展字段；
- 详情/列表的展示不走表单，另行用 `*Format` 调整。

### 6.2 混合模式（扩展字段）

`addOrEditOptions.props.columns` 的解析器可以接收**元数据未下发**的键：框架会提供一个只有
`prop` / `_column.key` 的空列（`handle-dialog.ts::resolveFormColumn`），页面可自行建立标签、
`valueType`、`renderField` 与 `colProps`。适用：独立端点读写的编辑区（如 API 应用的资源授权）、
需要注入候选集的 JSON 字段、需要跨字段联动校验的控件。

- 自定义控件优先 `renderField`（`registry.ts::registerFormRenderer` 只适合全局新类型）；
- 提交前用 `beforeSubmit` 做形态转换（数组↔逗号文本、`""`→`null`、去掉扩展字段键）；
- 主记录 + 附属端点需要在一次保存里完成时，用 `addOrEditOptions.apiReq` 串起来（返回最终主记录响应）；
  附属写失败要给出可读告警，`grants` 这类「全量替换」端点**加载失败必须跳过写入**，不能覆盖为空。

### 6.3 手写模式（登记豁免）

手写表单只用于 6 表最后一行的形态。门禁 `node scripts/check-handwritten-forms.mjs`
（`pnpm check:forms`，CI 已接入）在两种情况下要求显式豁免：

1. 文件所在模块存在 RePlusPage 页面（模板里 `<RePlusPage`）；
2. 单文件 `el-form-item` 超过 12 个。

豁免二选一：登记 `xadmin-client/scripts/handwritten-forms-exempt.json`（推荐，理由集中可审），
或文件头写 `<!-- @form-exempt: 理由 -->`。登记项文件删除/改名后必须清理，否则门禁失败。

### 6.4 已知边界与踩坑

| 现象 | 原因 / 处置 |
|---|---|
| JSONField 在表单里变成 JSON 编辑器 | `input_type=json`；页面用 `columns.<key>` 改 `valueType` 并 `delete column.renderField`（`renderField` 优先级更高） |
| 关联字段编辑态回显成数字/空白 | 后端 `attrs=["pk"]` 时下发的标量，而 `object_related_field` 默认选项值是对象；改页面为标量 `options`（见 `xadmin-client/src/views/system/post/utils/usePostFormOptions.ts`） |
| 需要按另一字段显示/隐藏 | 元数据协议无 linkage；改用「始终可见 + 提示文案 + 跨字段校验」（见 `xadmin-client/src/views/settings/login-policy/utils/useLoginPolicyFormOptions.ts`） |
| 跨字段校验拿不到实时值 | 用 `props.dialogDrawerOptions.onChange` 维护实时快照，规则 validator 读快照 + 当前字段值 |
| 时间字段提交空串被后端拒绝 | `time` 渲染器提交 `HH:mm:ss`；清空值需在 `beforeSubmit` 归一为 `null` |
| 表单里想展示只读统计值 | 用 6.2 注入扩展字段（`valueType: "text"` + `renderField` 渲染 tag），随主记录提交但后端忽略只读字段 |
| 页面只写了跨字段规则，别的必填项不再拦截 | 框架已按字段合并规则（`handle-dialog.ts::mergeFormProps`）；页面 `formProps.rules` 只需声明自己关心的字段，同名覆盖、其余保留 |
| 复用「就地编辑」的子组件（增删行 / 改行对象）后值不回写 | 字段渲染器拿到的是转换副本，子组件的就地变更父表单看不到；包一层适配组件 `deep watch` 回写服务端载荷并在挂载时归一（见 `xadmin-client/src/views/approval/rule/components/RuleLevelsField.vue`） |
| LabeledChoiceField 打开即报必填 / 下拉空白 | 显式声明的 `LabeledChoiceField` 默认 `required=True`（模型有默认值时按模型语义改 `required=False`）；元数据默认值是标量而接口回读是 `{value,label}`，新建态在 `row` 解析器里补 `{ value }` |
