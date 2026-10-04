# ADR-027：代码生成器（Model → 后端四件套 + 前端页面 + 菜单种子）

- 状态：已接受
- 日期：2026-09-13
- 关联：年度开发计划 2026.10-2027.09 §四 W11（G7）；[framework-cookbook.md](../architecture/framework-cookbook.md)
  （范式来源）；xadmin-docs `example/new-app-api.md` / `new-app-client.md` / `new-app-menu.md`（教程同源）；
  [ADR-015](ADR-015-reference-project-adoption.md)（重依赖红线 / 「脚手架不立项」边界）

## 背景

新业务模块的落地成本集中在「按仓库既有范式抄一遍」：序列化器（字段声明 + `extra_kwargs` + `table_fields`）、
ViewSet/FilterSet、路由注册、前端 `BaseApi` + `RePlusPage` 两件套、菜单与权限码（含 `model` 关联）。
各处细节（导入路径、`input_type`、basename、权限码命名）都有约定，手抄易错。

「脚手架不立项」（D5/T17）否决的是**运行期脚手架**：引入模板引擎/生成服务会新增长期维护面。
本项的形态不同——**一次性代码生成**：模板与仓库既有范式同源，无运行期依赖、无新第三方依赖，
产物是普通仓库代码，由开发者继续编辑并按常规门禁提交。

## 决策

### 1. 定位与边界

- **输入 = 已存在的 Django 模型**：模型仍是唯一真源，生成器**不改模型、不建迁移、不建文件外的东西**；
- **输出 = 仓库范式代码**：生成的代码与手写代码无差别（可读、可改），"生成即过门禁"
  （ruff check + ruff format / prettier + eslint + typecheck 口径）；
- 不做：迁移生成、模型生成、运行期模板渲染、可视化设计器、覆盖已有文件（除 `--force`）、
  包管理/依赖安装；
- 与 `demo` app 无关（demo 已按决策停更，生成器的真实验证对象是使用方的业务 app；测试用 `demo.Book` 仅作样本）。

### 2. 命令形态

```shell
python manage.py generate_crud demo.Book \
  [--component DemoBook] [--url-prefix api/demo/book] \
  [--frontend-dir demo/book] [--frontend-root ../xadmin-client] \
  [--parent <上级菜单 pk>] [--with-import-export] \
  [--output <后端输出根>] [--skip-frontend] [--skip-menu-seed] \
  [--dry-run] [--force]
```

- 位置：`common/management/commands/generate_crud.py`（框架级开发工具；不扩 `system` app，ADR-015 §5）；
- `--dry-run`：只打印产物清单与内容，不落盘（评审/CI 预览用）；
- `--force`：覆盖**文件**（含已存在的生成文件）；共享文件（`views.py`/`urls.py`）走**生成块**替换。

### 3. 产物清单

| # | 产物 | 落点 | 已存在时的策略 |
|---|------|------|----------------|
| 1 | 序列化器 | `<app>/serializers/<model>.py` | 存在即跳过（`--force` 覆盖） |
| 2 | ViewSet + FilterSet | `<app>/views/<model>.py`（views 为包时）或 `<app>/views.py` 生成块 | 生成块按标记替换（幂等：重复执行结果一致） |
| 3 | 路由 | `<app>/urls.py` | 不存在则创建；存在则在 `urlpatterns` 前插入注册行 + 顶部插入 import（幂等） |
| 4 | 应用配置 | `<app>/config.py` | 不存在则创建（`URLPATTERNS` + 白名单占位）；存在则跳过并提示 |
| 5 | 前端页面 | `<client>/src/views/<frontend-dir>/index.vue` | 存在即跳过 |
| 6 | 前端 API | 同上目录 `utils/api.ts` | 同上 |
| 7 | 前端逻辑 | 同上目录 `utils/hook.tsx` | 同上 |
| 8 | 菜单种子 | `<output>/loadjson/seed_<app>_<model>.json` | 存在即跳过（`--force` 覆盖） |

前端产物未显式给 `--frontend-root` 且同级目录不存在 `xadmin-client` 时，只打印内容不落盘（提示传参）。

### 4. 字段映射规则（确定性，可解释）

- **序列化器字段**：`pk` 在前，其余按模型字段声明序；自动字段（`auto_now*`/自动主键）不进 `fields`；
  审计字段（`creator`/`modifier`/`dept_belong`）默认保留只读语义（`BaseModelSerializer` 口径）；
- **关系字段 `extra_kwargs`**：
  - 关联 `UserInfo` → `{"attrs": ["pk", "username"], "format": "{username}({pk})", "input_type": "api-search-user"}`；
  - 其他关联 → `{"attrs": ["pk"]}`（前端回退默认渲染；数据量大时按 cookbook 换 `input_type`）；
  - `required` 取模型 `null=False, blank=False`，M2M 恒 `False`；
- **FilterSet**：文本类字段生成 `icontains` 自定义过滤器；`Meta.fields`（驱动前端搜索表单）收纳
  文本/布尔/choices/日期/关联字段，排除 JSON/文件/大文本与审计字段；`ordering_fields` 只要 `created_time`；
- **菜单权限码**：`动作:组件名`（`list/create/retrieve/partialUpdate/destroy`，`--with-import-export`
  追加 `exportData/importData`，路径 `export-data`/`import-data`），与 ViewSet 实际 action 对齐；
  路径正则与既有种子同口径（`<prefix>$`、detail 为 `<prefix>/(?P<pk>[^/.]+)$`，无前导 `^`，
  与 router `trailing_slash=False` 一致）；
- **菜单 `model` 关联**：查 `ModelLabelField(field_type=ROLE, name=<app.model>, parent=None)` 的 pk 写入
  种子（字段权限的数据源）；未找到时留空并提示先跑 `sync_model_field`；
- **种子 pk 确定性**：全部用 `uuid5`（固定命名空间 + `<app>:<model>:<role>` 键）生成，
  重复导入同一种子 = 覆盖同一批行（`loaddata` 语义），不会产生重复菜单。

### 5. 与门禁的关系

- 生成器单测（`tests/unit/common/test_generate_crud.py`）：
  - 对 `demo.Book` 生成到 `tmp_path`：断言产物清单齐全、`ast.parse`/`compile` 通过、
    `ruff check` + `ruff format --check` 通过（调用仓库内 ruff，与 CI 同口径）；
  - 断言关键结构：序列化器字段/`table_fields`/`extra_kwargs`、FilterSet 字段、权限码集合、
    种子 JSON 结构（menumeta/menu/permission）、uuid5 幂等（同输入两次生成内容一致）；
  - 覆盖保护：二次执行不改内容；`--force` 才覆盖（`views.py`/`urls.py` 的生成块为幂等合并而非覆盖）；
- 生成的前端页面 `locale-name` 缺省时按 `RePlusPage` 既有回退（后端 label）渲染，**不强制新增语言包词条**；
  需要中英词条的按教程补 `locales/{zh-CN,en}.yaml`（可选）；
- 生成物需要开发者复核的前两点（命令输出中提示）：关联字段 `input_type` 是否符合数据量、
  菜单是否需要挂到已有目录（`--parent`）。

### 6. 增量（2026-09-19，二次开发友好化 P2-4）

- **后续步骤清单**：命令输出尾部把散落在教程里的手工动作收敛为可复制命令——`XADMIN_APPS`
  注册提示（未登记时）、菜单种子 `loaddata` 灌库、`sync_model_field`（种子 model 关联为空时）、
  菜单/角色授权、`doctor` 自检；与 `docs/guide/first-module-30min.md` 口径同步；
- **`--with-module`**：同时生成 `{app}/modules.py` 模块声明（模块 id 默认 app label，
  `--module-id` / `--module-level` 可覆盖）；模板与 `generate_module` 同源
  （`common/core/modules/scaffold.py`，单一模板源，双仓命令不再各写一份），模块 id 已存在时
  降级为提示、不中断生成；元组字面量渲染为 ruff format 口径的双引号
  （`generate_module` 既有产物同步修正为一次过 `ruff format --check`）。

## 7. 增量（2026-10-04，GUI 字段级自定义与体验完善）

GUI（`system/utils/codegen_gui.py` + 客户端 `views/system/codegen/`）从「模型下拉 + zip」
升级为完整生成工作台；安全口径不变——**端点只读**（模型内省 + 字典查询 + 模板渲染 + zip
下载），落盘 / 入库仍由 CLI `--bootstrap` / `--register-app` 承担。引擎模板增强（CLI/GUI 同源）：

- **字段级自定义**（GUI 核心能力，域拆分见 `system/utils/codegen_fields.py`）：显示名覆盖
  （`extra_kwargs["label"]`，DRF 原生）、必填 / 只读覆盖、`input_type` 覆盖、**字典绑定**
  （生成 `DictChoiceField` 显式声明，整型值自动 `value_cast=int`）、表格列 / 搜索开关、
  字段排序（GUI 拖拽 → `serializer_fields` 顺序）。两条框架约束（越界即报错）：
  `input_type` 覆盖仅限**关联字段**（`fields_related` 只在关系字段上 pop `input_type`，
  非关联字段的渲染器由字段类型决定）；字典绑定仅限**非关联字段**（`DictChoiceField` 是
  `LabeledChoiceField` 子类，值为标量而非关联对象）。校验 input_type 须在
  `DECLARED_INPUT_TYPES` 词表或 `api-` 前缀族内、dict_code 须为启用中的字典类型行；
- **CLI/GUI 能力对齐**：GUI 新增 `with_tests` / `menu_parent`（菜单树选择）/
  `menu_icon`（菜单种子图标，默认仍是 `ep:document`）/ `module_id` / `module_level` /
  `skip_menu_seed`；CLI 对应新增 `--menu-icon`、`--dict-field 字段=字典code`（可多次）；
- **批量生成**：`download` 端点传 `models`（多模型清单）走批量打包——共享表单选项 +
  逐模型引擎默认字段计划；同路径产物去重保留首个，内容冲突（如各自 urls.py 注册行）
  附合并提示；NEXT_STEPS.md 合并为单文档分节；
- **zip 附 NEXT_STEPS.md**：`_print_next_steps` 的清单口径抽取为 `_next_steps`（CLI 打印 /
  GUI markdown 同源），产物预览树同步可见；
- **前端产物**：`hook.tsx` 自带自定义按钮注释样板（工具栏 `tableBarButtonsProps` /
  行内 `operationButtonsProps`，`code` 对应权限码 `动作:组件名`），范式指向
  `views/system/dict/utils/hook.tsx`；
- **生成方案本地保存**：GUI 表单 + 字段配置整体存浏览器 localStorage（命名保存 / 载入 /
  删除 / 导出导入 JSON）——代码生成不落库的定位不变，跨设备用 JSON 文件迁移；
- **预览体验**：文件树按仓库分组、highlight.js 语法高亮（按需注册语言、动态 import
  懒加载）、行号、单文件复制 / 下载、notice 徽标。
