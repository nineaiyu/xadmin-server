# demo（Book 示例 app）

> **定位：官方示例**——演示框架标准四件套 + 三条进阶链路（审批流 / 敏感操作二次确认 /
> 回收站与变更历史）的接入姿势，与框架同步演进。生产环境禁止启用；新业务模块请以
> `python manage.py generate_crud <app>.<Model>` 为起点（教程见 `docs/guide/first-module-30min.md`）。

## 它演示什么

| 能力 | 位置 | 说明 |
|------|------|------|
| 标准四件套 + 元数据驱动页面 | `models.py` / `serializers/book.py` / `views.py` / `urls.py` | choices / FK / M2M / 图片与附件字段（`ProcessedImageField`、`UploadFile` 关联）、`tabs` 分组表单、`table_fields`、`extra_kwargs`（关联输入形态 / 文件字段忽略权限）、自定义 `input_type` |
| **上架审批（审批流引擎接入）** | `services.py` + `views.py::submit` | 提交上架 → `create_instance(biz_type="demo_book")` → 终态经 `approval_instance_finished` 信号回写 `Book.status`（通过 → 已上架并启用并落 `on_shelf_time`、驳回 → 已驳回）；前端「提交上架」按钮 + 状态彩色标签 |
| **二次确认（敏感操作审批）** | `views.py` 的 `destroy` / `batch_destroy` + `@ApprovalRequired()` | 删除前先走审批（412 协议）：所有用户一律拦截、申请人不能自审；批准后重发请求由前端自动携带一次性令牌 |
| **回收站（软删除）** | `models.py` 继承 `SoftDeleteModel` + `views.py` 混入 `RecycleBinAction` | 删除进回收站（`recycle` 列表 / `recycle/restore` 恢复 / `recycle/purge` 物理清除）；前端 `recycleBin` 抽屉按 `auth.recycleList` 显隐 |
| **变更历史（行级审计）** | 前端 `auth.changeHistory` + 权限点 `changeHistory:DemoBook` | 行操作「变更历史」按 detail 路由回溯该行操作日志（含字段级 diff） |
| **定时任务（自动流转）** | `tasks.py` + 种子周期任务「示例-自动下架书籍」 | `@shared_task` + `PeriodicTask`；任务管理页可启停 / **立即运行**，滞销书自动回草稿（默认停用） |
| 批量操作 / 导入导出 / 自定义 action | `views.py` | `batch-destroy`、`import-data` / `export-data`、`push` 自定义动作 |

## 二开抄作业地图（要做什么 → 抄哪段）

| 你要做的 | 抄哪里 | 关键点 |
|---|---|---|
| 新模块从 0 到 1 | 无需手抄：`generate_crud` 生成后端四件套 + 前端页面 | 教程 `docs/guide/first-module-30min.md`；demo 是"手写理解版"对照物 |
| 字段 / 表单分组 / 列表列怎么摆 | `serializers/book.py` 的 `tabs` / `table_fields` / `extra_kwargs` | 声明式字段同时驱动 `search-columns` 元数据与前端渲染 |
| 列表单元格自定义渲染 | 前端 `views/demo/book/utils/hook.tsx` 的 `listColumnsFormat` | 状态彩色标签、价格格式化两个 `cellRenderer` 示例 |
| 关联字段的输入与内联展示 | `serializers/book.py` 的 `extra_kwargs`（`attrs` / `format` / `many` / `api-search-user`） | 表单里是搜索选择器，列表里内联显示 `{name}` |
| 附件 / 图片（含多尺寸缩略图） | `models.py` 的 `file` / `files` / `avatar`(scales) | `UploadFile` 关联即得附件中心复用；物理清除才走文件清理链 |
| 自定义行操作按钮 | `views.py::push` + 前端 `operationButtonsProps` | 前后端各一处；按钮显隐由权限点控制 |
| 批量删除（纳入审批） | `views.py::batch_destroy` + `@ApprovalRequired()` | 412 协议、批准后令牌重放（前端自动） |
| 审批流接入（提交/回写终态） | `services.py` + `views.py::submit` | `create_instance` + `approval_instance_finished` 信号；**审批判断不进业务代码** |
| 软删除 / 回收站 | `models.py` 继承顺序 + `views.py` 混入 `RecycleBinAction` | `SoftDeleteModel` 放 MRO 首位；软删不级联，`hard_delete()` 才清理文件 |
| 变更历史（行级审计） | 前端 `auth.changeHistory` + 权限点（查操作日志端点） | 框架自带字段级 diff，无需自建审计表 |
| 定时任务（自动流转 / 清理） | `tasks.py` + 周期任务种子 | `shared_task` 声明即注册；幂等 + 返回可读统计进任务执行记录 |
| **数据权限 / 字段权限（零代码）** | 系统 →「数据权限 / 字段权限」页面配置 | Book 继承 `DbAuditModel`（含 `dept_belong`），全局过滤器自动生效——权限是配置而非编码 |
| 给模块写测试 | `tests/integration/demo/` 五个文件 | 集成测试用真实请求断言；任务直接调用即同步执行 |

## 启用与演示（开发环境）

```bash
# 1. 启用 demo app：config.yml 写 XADMIN_APPS: [demo]（或删除 config.yml 走开发兜底默认）
# 2. 一键就绪（幂等）：示例菜单 + 19 个权限点 + 「示例-书籍上架」流程 + 删除二次确认开关
#    + 3 条示例书籍（开箱即演示）+ 周期任务「示例-自动下架书籍」（默认停用）
python manage.py seed_demo_book
#    随演示数据全家桶一起装：python manage.py seed_demo_all
```

演示路径：

1. 侧栏「示例 → 图书管理」：标准列表页（搜索 / 列渲染 / 导入导出 / 批量删除），已有 3 条示例书籍；
2. 行操作「提交上架」→ 书籍状态变「审批中」（前端二次确认弹窗）；
3. 「审批中心 → 待办列表」用**另一个账号**（默认审批人 `xadmin,isummer`，可在流程设计器调整）
   通过 → 回到列表刷新，状态变「已上架」、`is_active` 与上架时间自动落上；
4. 删除一条书籍 → 弹「已提交审批」（412）：用审批人批准后**再点一次删除**，前端自动携带
   令牌完成删除；批量删除同口径；
5. 工具栏「回收站」：查看已删除书籍（含删除时间）→ 勾选**恢复**（回到主列表）或**物理清除**；
6. 任意行「变更历史」：查看该行的操作日志与字段级变更 diff（谁在何时改了什么）；
7. 「系统 → 任务管理」找到「示例-自动下架书籍」（默认停用）→「立即运行」：把某条书籍
   `on_shelf_time` 改到 31 天前再运行，可见其自动回「草稿」并停用；任务执行记录里能看到返回统计；
8. （可选，零代码）「系统 → 数据权限」建规则（选 DemoBook 菜单）分配给某个非超管用户 →
   该用户登录后列表只按其部门/只读范围返回数据——演示"权限是配置项，不是代码"。

> 关闭演示：`python manage.py seed_demo_book --clean-only`
> （移除菜单 / 权限点 / 流程 / 拦截清单 / 示例书籍 / 周期任务）。

## 边界与注意事项

- **迁移**：`demo/migrations/0001_initial.py` 为**合并后的单文件**（含全部字段，含 `deleted_at` /
  `on_shelf_time`）；全新库直接 `python manage.py migrate demo` 即建全表。
  **跑过旧版 demo 迁移的开发库/演示库**（曾拆分为 0001 + 0002）请重置（演示数据无需保留）：
  ```sql
  DELETE FROM django_migrations WHERE app = 'demo';
  DROP TABLE IF EXISTS demo_book_managers, demo_book_managers2, demo_book_files, demo_book;
  ```
  ```bash
  python manage.py migrate demo
  ```
- **权限点清单**：demo 路由不参与 loadjson 权限点扫描（既定口径），新增 action 时必须同步
  `seed_demo_book.py` 的 `PERMISSION_PLAN`（守护测试
  `tests/unit/system/test_seed_demo_book.py` 会因清单与 ViewSet 不一致而失败）；
- **二次确认开关**：写入 `APPROVAL_REQUIRED_PATHS`（系统配置）；单账号环境无法自审——
  演示第 4 步需两个账号（或为普通账号授权后由其发起、超管批准）；
- **回收站保留期**：`RECYCLE_BIN_RETENTION_DAYS`（默认 30 天）之外的记录由清理任务物理清除；
  `recycle/purge` 不传 `pks` 即清理过期数据；
- **示例书籍**：按固定名称清单幂等识别；库里无可用用户时自动跳过（不阻断其余种子）；
  软删过的示例数据重复执行 `seed_demo_book` 会自动复活；
- **周期任务**：种子只建配置（`enabled=False`），执行依赖 celery beat；任务管理页「立即运行」
  走 worker，本地演示需 worker 在线；
- **审批人**：种子默认 `xadmin,isummer`（双环境占位），提交时按实际存在的用户生效；
  全都不存在时提交会被引擎 fail-closed 拒绝（提示无可用审批人）；
- **`block`** 为可写开关字段（`input_type="boolean"` 的渲染与提交示意）。

## 文档引用

- 框架能力速查与覆写红线：`docs/architecture/framework-cookbook.md`
- 数据权限 / 字段权限原理：`docs/architecture/data-permission.md` / `field-permission.md`
- 新人上手路径：`docs/architecture/overview.md` §十
- 手写教程（与本文代码同源）：xadmin-docs 的 `example/new-app-api.md` / `new-app-client.md`
- 测试：`tests/integration/demo/`（CRUD / 权限 / 上传 / 导入导出 / 上架审批与二次确认 / 定时任务）
