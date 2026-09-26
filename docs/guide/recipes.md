# 常见扩展流程（处方集）

> 定位：按**任务**索引的步骤化处方——"我想做 X，从哪下手"。每条 = 场景 → 步骤 → 验证。
> 组件是什么/怎么配见 [architecture/component-handbook.md](../architecture/component-handbook.md)；
> 第一次建模块见 [first-module-30min.md](first-module-30min.md)；踩坑先查 [dev-pitfalls.md](../dev-pitfalls.md)。
>
> 通用纪律（每条处方都适用）：**新功能 100% 携带测试**；改后端代码后重启进程
> （`docker compose restart server celery-worker celery-heavy celery-beat`）；
> 改后端后跑 `pnpm test:e2e:fresh`；新增端点必须登记权限点（R2/§四）。

## 一、模型与接口

### R1 给已有模块加一个字段（端到端）

**场景**：`Customer` 模型加一个"客户等级"字段，前端列表/表单/搜索自动出现。

| 步骤 | 操作 |
|---|---|
| 1. 模型 | `crm/models.py` 加字段（`verbose_name` 用 `_()` 包裹） |
| 2. 迁移 | `python manage.py makemigrations crm && python manage.py migrate crm` |
| 3. 序列化器 | `Meta.fields` 加字段（进接口）；需要进列表默认列再加 `Meta.table_fields` |
| 4. 搜索（可选） | 需要搜索 → 过滤器声明 + 列入 `Meta.fields`（见 R4） |
| 5. 字段权限树 | `python manage.py sync_model_field`（幂等；字段权限/字段树同步） |
| 6. 重启进程 | 元数据缓存与路由在进程内，必须重启 |

**验证**：`python manage.py doctor` 无缺口 → 刷新页面（列表列出现、表单可填、搜索可用）；
若列仍缺失，按顺序查：`Meta.table_fields` → 重启 → DEV 页面元数据警示条。

### R2 加一个自定义动作（`@action`）+ 前端按钮

**场景**：给客户列表加"标记重点"按钮，点击调一个后端动作。

| 步骤 | 操作 |
|---|---|
| 1. 后端动作 | ```python @action(methods=["post"], detail=True); @extend_schema(summary="标记重点"); def mark(self, request, pk): ...; return ApiResponse(data=...)```（**docstring/summary 必写**：菜单显示名与操作日志 module 取自它） |
| 2. 权限点 | `python manage.py sync_menu_permissions --dry-run` 查缺口 → 执行补齐；要固化进种子加 `--update-seed`。（**PUT 方法需手工登记**，生成器不产出） |
| 3. 前端 API | `src/api/system/crm.ts`（或模块 api 文件）里的 Api 类加方法：`mark = (pk) => this.request<BaseResult>("post", {}, {}, \`${this.baseApi}/${pk}/mark\`)` |
| 4. 前端按钮 | hook 的 `operationButtonsProps.buttons` 加一项：`{ text, code: "mark", show: auth.mark && 10, confirm: {...}, onClick: ({ row, loading }) => handleOperation({ t, apiReq: api.mark(row.pk), requestEnd: refresh }) }` |
| 5. 权限映射 | hook 的 `auth` 里加 `mark: hasAuth("mark:CustomerViewSet")`（或 `getDefaultAuths(instance, ["mark"])`） |

**验证**：超管点按钮功能正常 → 换非超管（已授权）可见可用；未授权角色按钮隐藏且直调接口 403。

### R3 加一个非 CRUD 接口（自定义动作的放大版）

**场景**：一个不围绕单模型的动作型端点（如"一键同步"）。

- 优先复用 ViewSet + `@action(detail=False)`（自动获得权限链、审计、统一响应）；
- 完全独立的端点（如运维类）参考 `common/api/`（内核只读端点）或 `system/views/auth/`（免鉴权类）的写法；
- 返回一律 `ApiResponse`；**新增端点后跑 `sync_menu_permissions`** 登记权限点，再跑 `doctor` 复核。

### R4 加搜索/筛选字段

1. FilterSet 声明过滤器（`filters.CharFilter(field_name="name", lookup_expr="icontains")`）；
2. **同时列入 `Meta.fields`**（否则 `search-fields` 元数据不产出，前端搜不到）；
3. 关联字段搜索用 `PkMultipleFilter(input_type="input" | "api-search-user")`；大表搜索见 R5；
4. 排序白名单 `ordering_fields`（自动生成 `select-ordering` 搜索项）。

**验证**：搜索区出现该字段；`GET {base}/search-fields` 返回该字段（含 `input_type`）。

### R5 大表关联字段走远程联想（suggestions）

**场景**：关联表数据量大（用户/部门/大业务表），表单下拉或搜索不能用全量 choices。

- **表单字段**：序列化器 `extra_kwargs` 加 `input_type="api-search-user"`（用户）或复用 `api-search-*` 组件；在前端 `views/system/apiSearch.ts` 注册组件（已有 user/dept/role/menu 四款）；
- **搜索字段**：`PkMultipleFilter(input_type="api-search-user")`；
- **联想（候选集与写入校验同源）**：ViewSet 声明 `suggestion_fields = ("delegate",)`，前端自动升级为 `SuggestSelect`（远程/防抖/回显）；
- 详见 [ADR-043](../adr/ADR-043-remote-suggestions.md) 与组件手册 §1.4。

### R6 加导入导出能力

1. ViewSet 混入 `ImportExportDataAction`（完整）或 `OnlyExportDataAction`（只导出）：
   ```python
   class BookViewSet(BaseModelSet, ImportExportDataAction): ...
   ```
2. 权限点：`exportData:*` / `importData:*`（及 `-async/-validate/-headers` 链）由生成器种子或 `sync_menu_permissions` 登记；**导入导出链必须空绑定模型**（字段权限按运行时回退到 list/create）；
3. 序列化器可选 `Meta.fields_unexport` 排除导出列；列顺序/验证模板自动生成；
4. 前端 `<RePlusPage>` 检测到 `auth.exportData` / `auth.importData` 自动显示按钮（`allowAsyncExport` 控制异步弹层）。

**验证**：导出 CSV/XLSX（含中文不乱码）→ 修改后经"导入校验"回传 → 错误行定位提示。

### R7 枚举接入数据字典（文案可运营化）

**场景**：状态/类型等枚举的文案与颜色需要管理员在线维护（不发版）。

1. 服务端：序列化器字段用 `DictChoiceField(dict_code="xxx", fallback_choices=Model.Choices.choices, value_cast=int, merge_fallback=True)`——`fallback_choices` 必传（字典未配置时回退模型枚举）；
2. 被代码按 code 引用的字典类型在 `loadjson/datadict.json` 登记（类型行 `is_locked=true`）；
3. 前端零成本（元数据自动下发；非元数据场景用 `useDict(code)` / `getDictItems(code)`）；
4. 完整口径（展示型 vs 写入型、`merge_fallback` 语义、缓存失效）见 [框架开发遵循准则.md](../框架开发遵循准则.md) §1.9。

### R8 加软删除与回收站

1. 模型继承 `SoftDeleteModel`（**MRO 首位**）：`class Customer(SoftDeleteModel, DbAuditModel)`；
2. ViewSet 混入 `RecycleBinAction`；
3. 前端 `<RePlusPage :recycle-bin="true">`（需 `auth.recycleList`；默认列表自动过滤软删行）；
4. 保留期与物理清理：`RECYCLE_BIN_RETENTION_DAYS` + 内置 `purge_soft_deleted` 周期任务自动处理；
5. 批量删除语义：软删/文件清理类模型框架自动走逐行 `perform_destroy`（覆写红线见 [framework-cookbook.md](../architecture/framework-cookbook.md) §四）。

## 二、前端页面

### R9 定制列表列 / 表单（页面级，最常用）

| 目标 | 出口 | 示例 |
|---|---|---|
| 改单元格渲染 | `listColumnsFormat` | `switch (column._column?.key) { case "status": column.cellRenderer = ... }` |
| 改搜索控件 | `searchColumnsFormat` | 同上模式 |
| 改新增/编辑表单（隐藏字段/换控件/加规则） | `addOrEditOptions.props.columns`（解析器 ctx：`column / isAdd / formValue`）+ `baseColumnsFormat` | `views/system/user/utils/useUserColumnFormats.tsx` |
| 提交前加工（加密/拼装） | `addOrEditOptions.beforeSubmit` | 同上（用户页 AES 加密入口） |
| 行数据二次加工 | `searchResultFormat` | 按需 |
| 换提交接口 | `addOrEditOptions.apiReq` | 非标准接口场景 |

**约定**：判定字段用 `column._column?.key`（原始元数据在 `_column`）；LabeledChoice 系列行值是 `{value,label,color?}` 对象，取标量用 `.value`。

### R10 自定义 `input_type` 渲染器（全局级）

**场景**：后端下发一个新 `input_type`（或覆盖内置行为），所有页面生效。

1. 前端在**模块顶层**（早于页面首渲染）注册：
   ```ts
   import { registerDetailRenderer } from "@/components/RePlusPage";
   registerDetailRenderer("my_type", (item, ctx) => { item.render = ...; item.cellRenderer = ...; });
   ```
2. **四通道互不兜底**：搜索 / 表单 / 详情 / 列表分别注册（`registerSearchRenderer` / `registerFormRenderer` / `registerDetailRenderer`）；对象/数组值必须**同时**给详情 `render` 与列表 `cellRenderer`；
3. 在 `RePlusPage/src/utils/__tests__/renderers-pairing.spec.ts` 的分类清单登记（未登记即测试失败）；表单不可编辑的类型登记 `FORM_EXEMPT_TYPES` 说明理由；
4. 后端侧：`common/drf/metadata.py::get_field_type` 加 isinstance 分支（若引入新字段类）+ 契约同步（改 Schema 时 `pnpm sync:contract`）；
5. 验证：`pnpm vitest`（配对守护）+ `pnpm typecheck`（strict 全仓单轨）+ 目标页面人工核验。

### R11 加一个独立页面（非 RePlusPage）

**场景**：仪表盘、画布、聊天式页面等非标准 CRUD 形态。

1. 前端：`src/views/<模块>/<页面>/index.vue`（`defineOptions({ name: "XxxYyy" })`）；
2. 后端菜单：菜单管理新增**菜单类型**记录——`name` = 组件名、`component` = `src/views` 下相对路径（如 `crm/dashboard/index`）、`path` = 路由路径、`meta` 标题/图标；或写入种子（R20 的 `--update-seed` 口径）；
3. 若页面调接口：按 R2 登记权限点，前端 `getDefaultAuths` / `hasAuth` 控制按钮；
4. 词条：`locales/zh-CN.yaml` + `en.yaml` 成对补；
5. 验证：侧栏进入 → 刷新保持 → 非超管授权后可用。

### R12 加一个弹窗 / 抽屉

按组件手册 §2.2 的收敛模式：

```ts
// hook.ts 或页面逻辑里
addDialog({
  title: t("crm.customer.importTitle"), width: dialogSize("md"),
  draggable: true, destroyOnClose: true, closeOnClickModal: false,
  contentRenderer: () => h(CustomerImportForm, { onSaved: refresh })
});
```

- 内容组件：`components/XxxForm.vue`（reactive 表单 + `getPayload()` 校验，返回 `null` 表示保持弹窗）；
- 提交成功：**先 `done()` 关弹窗、再 `await` 刷新列表**；API 失败要 `.catch` 归一（否则 `beforeSure` 抛错 → loading 悬挂）；
- 复杂详情用 `addDrawer`；一句确认用 `ElMessageBox`（危险操作加 `el-button--danger` + `.catch` 兜底）；
- 复用"弹层表单"现成能力：`openDialogDrawer({...})`。

### R13 补 i18n 词条

1. `locales/zh-CN.yaml` 与 `locales/en.yaml` **同一路径成对补**（缺一即守护测试失败）；
2. 页面词条挂在 `locale-name` 命名空间下（如 `crmCustomer.xxx`）；菜单标题挂 `menus.xxx`；
3. 列 label 优先读 `{localeName}.{key}`，未命中回退 `commonLabels.*` → 后端 label；
4. 注意 yaml 重复 key 会白屏（vite i18n 报 "Map keys must be unique"）。

### R14 页面内嵌表格选择器（选数据）

**场景**：表单/筛选里选"某个业务对象"，且希望带搜索与分页。

- 内嵌选择（下拉里带表格）：`<RePlusSearch :api="xxxApi" v-model="selected" />`；
- 表单字段内嵌：给字段 `input_type` 配 `api-search-*` 组件（R5）；
- 数据源要"全量清单"（不分页）：`fetchAllRows(api)` 逐页拉全（防 100 条截断）。

## 三、平台能力

### R15 加定时任务

```python
from common.celery.decorator import register_as_period_task


@register_as_period_task(crontab="23 3 * * *", name="clean_xxx", module="crm", description="清理 XXX")
def clean_xxx_job(): ...
```

1. 放 `{app}/tasks.py`（`autodiscover` 自动发现）；
2. `module=` 填所属可裁剪模块 id（模块停用即不注册；内核任务留空）；
3. 重启进程后自动注册（任一 Django 进程启动时执行 `create_or_update_registered_periodic_tasks`，幂等）；
4. 重活在本应用 `config.py` 声明队列路由（走 heavy 队列），无需改 settings 工程层：
   ```python
   # {app}/config.py
   TASK_ROUTES = {"{app}.tasks.convert_xxx": "heavy"}  # 值也可用 {"queue": "heavy"}
   ```

**验证**：`/api/system/tasks/periodic` 列表出现该任务；「立即运行」可手工触发；`doctor` 无模块相关告警。

### R16 加消息通知（业务 → 用户）

1. 定义消息类（`{app}/notifications.py`）：
   ```python
   @register_message
   class CustomerAssignedMessage(UserMessage):
       category = "CRM"
       category_label = _("CRM")
       message_type_label = _("Customer assigned")

       def __init__(self, user, customer):
           self.customer = customer
           super().__init__(user)

       def get_html_msg(self):
           return {"subject": ..., "message": render_to_string(...)}
   ```
2. 发送：`CustomerAssignedMessage(user, customer).publish(is_async=True)`；
3. 新渠道（短信/IM 服务商）：`notifications/backends/<name>.py` 暴露模块级 `backend` + `BACKEND` 枚举补项 + `register_backend_msg` 映射渲染方法——三步即接入（现有 6 渠道）；
4. 免打扰/订阅由通知中心统一处理，业务代码不必判断。

### R17 业务接入审批流

参考实现：请假业务 `approval/utils/leave.py`（含注释版四步）。

1. **提交**：`instance, error = create_instance(flow=<流程>, applicant=user, title=..., form_data={...}, biz_type="leave", biz_id=str(obj.pk))`——`biz_type/biz_id` 是业务绑定，引擎不感知业务字段；
2. **回写**：监听终态信号 `system.signal.approval_instance_finished`，按 `instance.biz_type / biz_id` 更新业务状态（信号里做幂等；引擎侧失败不阻断审批）；
3. **流转**：审批动作（通过/驳回/加签/催办/委托）全部在流程审批中心完成，业务页只展示状态与轨迹（`ApprovalInstance` 查询）；
4. 前置：流程/节点/审批人在流程设计器配置；`biz_type` 常量放本业务模块。

**验证**：提交 → 生成实例与任务 → 审批通过 → 业务状态回写；驳回/撤回路径同样回写。

### R18 对外投递 Webhook 事件

1. 事件登记：`system/utils/webhook.py::EVENT_CATALOG` 加工件条目（含 label 与 payload 契约说明；契约有守护测试）；
2. 发射：业务终态处调 `emit_webhook_event("crm.customer.created", {"pk": ..., "name": ...})`（**唯一发射口**：吞异常、不阻断业务）；
3. 订阅方在「集成 → Webhook 订阅」页配置；投递记录在「投递审计」查询（签名 HMAC-SHA256 + 指数退避重试 + 耗尽告警）；
4. 新增事件的 payload 只放摘要字段，敏感数据不放事件体。

### R19 让模块可裁剪

1. `python manage.py generate_module crm --app crm --level optional --route ^/api/crm/` 生成 `crm/modules.py`（`ModuleSpec` 声明：menus / permissions / routes / ws_routes / depends）；
2. `python manage.py modules` 验证清单与依赖闭包；
3. 使用方在 `config.yml` 用 `MODULE_PRESET` / `MODULE_ENABLE` / `MODULE_DISABLE` 控制；
4. 注意：模块 id 冲突会降级为提示；`depends` 声明依赖后自动闭包。

### R20 升级 / 发布收尾

```bash
python manage.py post_upgrade      # 种子 + 语言包 + 缓存失效 + 权限缺口扫描（幂等）
python manage.py doctor            # 八项自检，修到全绿
```

新增权限点必须在发布前入库：`sync_menu_permissions --update-seed`（回写种子，随版本发布）或生成器种子 `loaddata`。

## 四、验证与测试（红线：新功能 100% 携带测试）

### R21 后端测试

| 目标 | 做法 |
|---|---|
| 单测（不发 HTTP） | `tests/unit/<app>/test_*.py`，`pytest` fixture：`db` / `monkeypatch`；sqlite `:memory:` + FakeRedis（零外部依赖） |
| 集成（发 HTTP） | `tests/integration/`，`api_client` / `superuser` / `auth_client` / `menu_factory` 等 fixture（`tests/conftest.py`） |
| 权限/菜单相关 | 造 `menu_factory` 菜单 + 权限点，断言非超管行为 |
| 守护测试 | 覆写点/协议/种子类改动都要求补守护测试（框架有先例可抄） |
| 运行 | `pytest -n auto`；提交前跑全量（2800+） |

### R22 前端 E2E 补一条

1. 新 spec 放 `xadmin-client/e2e/*.e2e.ts`（命名 `<场景>.e2e.ts`）；
2. 用 `helpers.ts` 的登录/菜单跳转（**SPA hash 路由切换必须整页 goto + 面包屑确认**）；
3. 需要专属数据时在 `scripts/e2e_seed.py` 加场景建造函数（幂等，双浏览器共享库）；
4. 定位纪律：锚定本 run 唯一文本、避开会话/历史残留 DOM；断言状态用双语或正则兼容；
5. 运行 `pnpm test:e2e -- e2e/xxx.e2e.ts`（改后端先 `pnpm test:e2e:fresh`）；
6. 陷阱表见 `xadmin-client/e2e/README.md`（先查再写，能省一轮返工）。

### R23 加一个 AI 受限动作（AI 助手可执行）

AI 助手只产出「动作草稿」，用户确认后才以**本人身份**执行——新增动作 = 在注册表加一条 `ActionSpec`：

1. 注册表：`system/utils/ai_actions.py::ACTION_SPECS`（唯一白名单，LLM 输出按不可信输入处理）；
2. 实现三件套（同文件内私有函数即可）：
   - `validate(user, params)`：逐项校验，返回 `(JSON 安全参数, 错误文案)`（错误文案会展示给用户）；
   - `execute(user, params)`：执行并返回 `{ok, detail, data}`；creator / 申请人恒为发起用户，**不接受任何「替他人」参数**；
   - `requires_approval(user, params)`（是否需 412 二次确认）与 `available(user)`（动作对当前用户是否可用）；
3. 登记 `ActionSpec`（`params` 是给 LLM 构造草稿的参数 schema）：

   ```python
   AI_XXX_KEY = "xxx.submit"

   ACTION_SPECS[AI_XXX_KEY] = ActionSpec(
       key=AI_XXX_KEY,
       label=_("..."),
       description=_("..."),  # 进 LLM 目录，同时展示在确认卡片
       params={...},
       required_visits=(("POST", "/api/system/xxx"),),  # 所需底层业务权限点（与菜单 path 同口径）
       validate=_validate_xxx,
       execute=_execute_xxx,
       requires_approval=_never_requires_approval,
       available=_always_available,
   )
   ```

4. 三条红线（与注册表模块 docstring 同源）：
   - **权限双门**：AI 执行端点权限 × `required_visits` 业务权限点（`user_can_visit` 与菜单权限同一匹配函数），缺一不可；
   - **可审计**：执行落 `OperationLog(module=AI:action, auth_type=ai)`；需要二次确认的复用敏感操作审批 412 协议；
   - **灰度**：`AI_ACTION_ENABLED`（AI 配置页开关）默认关，打开后动作才会进入 prompt 目录。
5. 验证：与 AI 对话发起动作（聊天 `/do` 命令或自然语言）→ 出现草稿卡片 → 确认后执行成功；
   补单测覆盖 `validate` 边界与 `has_permission` 双门（仓库既有测试可抄）。

## 五、相关文档

| 文档 | 内容 |
|---|---|
| [architecture/component-handbook.md](../architecture/component-handbook.md) | 组件职责 / 用法 / 配置 / 扩展点 |
| [architecture/方案选型与对比.md](../architecture/方案选型与对比.md) | 方案选择与对比 |
| [first-module-30min.md](first-module-30min.md) | 从零建模块 |
| [framework-cookbook.md](../architecture/framework-cookbook.md) | ViewSet 覆写红线、Action↔BaseApi |
| [dev-pitfalls.md](../dev-pitfalls.md) | 高频坑（静默失败类） |
| [框架开发遵循准则.md](../框架开发遵循准则.md) | 服务端/前端统一约定 |
