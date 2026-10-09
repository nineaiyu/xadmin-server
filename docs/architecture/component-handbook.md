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
| 换单元格/表单控件 | `*Format` 出口（页面级）或渲染器注册（全局级） |
| 弹一个表单/详情 | `addDialog` / `addDrawer` / `openDialogDrawer` |
| 选一个用户/部门/角色 | `api-search-user` 等（表单）或 `RePlusSearch`（独立选择器）或联想（大表搜索） |
| 大表关联字段搜索 | `PkMultipleFilter` + `SuggestionsAction` + 前端自动 `SuggestSelect` |
| 定时跑一件事 | `@register_as_period_task(interval=..., module=...)` |
| 发一条多通道通知 | 消息类实例 `.publish(is_async=True)`（新渠道加 `backends/<name>.py`） |
| 让业务走审批 | `approval_flow.engine.create_instance(biz_type=..., biz_id=...)` + 终态信号 |
| 对外投递事件 | `task/utils/webhook.py::emit_webhook_event`（事件先登记 `EVENT_CATALOG`） |
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
| 值级加密 | `packages/xadmin-common/common/base/utils.py::signer` | 敏感字段入库前 | webhook secret / AI api_key |
| 新增缓存类 | `packages/xadmin-common/common/cache/storage.py::RedisCacheBase` | 键名过 `check_cache_keys.py` | — |
| 周期任务 | `@register_as_period_task(module=...)` | module 归属可裁剪 | `system/tasks/` |
| 通知渠道 | `notifications/backends/<name>.py`（模块级 `backend`） | 渠道枚举补 `BACKEND` | `notifications/backends/email.py` |
| 通知消息类型 | `@register_message` + `register_backend_msg` | 渲染映射补齐各渠道 | `notifications/notifications.py` |
| Webhook 事件 | `EVENT_CATALOG` 登记 + `emit_webhook_event` | 事件契约守护测试 | `task/utils/webhook.py` |
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

