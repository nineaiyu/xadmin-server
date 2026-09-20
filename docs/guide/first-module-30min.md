# 30 分钟：开发第一个业务模块（二次开发快速通道）

> 前置：已完成根 [README](../../README.md) 的「快速开始」（`bash utils/dev_up.sh`），能用 `xadmin` 登录。
> 目标：从零得到一个**完整可用**的业务模块——后端模型 + REST 接口 + 前端列表页 + 菜单权限，
> 非超管可按角色授权使用。全程只需了解三条约定，不需要读框架源码。

## 时间分配（总计约 30 分钟）

| 步骤 | 内容 | 预算 |
|------|------|------|
| 0 | 认识「活样例」demo.Book | 5 min |
| 1 | 建 app + 定义模型 | 5 min |
| 2 | 注册 `XADMIN_APPS` + 迁移 | 3 min |
| 3 | `generate_crud` 生成四件套 + 前端页面 + 菜单种子 | 5 min |
| 4 | 装载菜单种子 + 角色授权 | 7 min |
| 5 | 验收与自检（doctor） | 5 min |

**三条约定**（先记住，后面都会遇到）：

1. 新业务一律**独立 app**，并在 `config.yml` 的 `XADMIN_APPS` 注册（否则不参与路由与迁移）；
2. 前端列表页由后端**元数据**驱动：序列化器的 `fields` / `table_fields` 决定表格列与表单；
3. 权限码是 `动作:组件名`（如 `list:CustomerViewSet`），**必须先入库授权**（否则非超管 403）。

## 步骤 0：认识活样例 demo.Book（5 min）

demo app 在「无 config.yml 的开发兜底」下默认启用（`XADMIN_APPS: [demo]`），它就是一个标准四件套：

| 层 | 文件 | 说明 |
|----|------|------|
| 模型 | `demo/models.py`（`Book`） | 继承 `DbAuditModel`（自动带 pk / created_time / updated_time / creator） |
| 序列化器 | `demo/serializers/book.py` | `fields` = 接口字段；`table_fields` = 列表默认列 |
| 视图 | `demo/views.py`（`BookViewSet`） | 继承 `BaseModelSet`（CRUD/批量/元数据/回收站开箱即用） |
| 路由 | `demo/urls.py` + `demo/config.py` | `config.py::URLPATTERNS` 由 `XADMIN_APPS` 自动注入总路由 |
| 前端 | `xadmin-client/src/views/demo/book/` | `index.vue`（一行 `RePlusPage`）+ `utils/{api.ts,hook.tsx}` |

> demo 没有内置菜单——页面还不能从侧栏进入。这正是你要练的第一个动作（步骤 4）。

## 步骤 1：建 app 与模型（5 min）

```bash
cd xadmin-server
python manage.py startapp crm
```

编辑 `crm/models.py`：

```python
from django.db import models

from common.core.models import DbAuditModel


class Customer(DbAuditModel):
    """客户（示例）。verbose_name 会直接成为前端表单/列标题。"""

    name = models.CharField("客户名称", max_length=64)
    level = models.SmallIntegerField("等级", choices=[(0, "普通"), (1, "VIP")], default=0)
    phone = models.CharField("联系电话", max_length=32, blank=True, default="")
    remark = models.TextField("备注", blank=True, default="")
```

## 步骤 2：注册应用 + 迁移（3 min）

在 `config.yml` 注册（无配置文件时先 `cp config_example.yml config.yml`；注意开发兜底默认值是 `[demo]`，改成两个）：

```yaml
XADMIN_APPS: [demo, crm]
```

```bash
python manage.py makemigrations crm
python manage.py migrate crm
```

> 注册顺序：`crm/config.py`（`URLPATTERNS` 所在处）由下一步的 `generate_crud` 生成，此刻尚不存在——
> 启动日志会出现一条「缺少 crm/config.py 的 URLPATTERNS，路由未注入」告警，属**预期**：
> 生成 `config.py` 并重启进程后路由才会注入（`python manage.py doctor` 可核对）。

## 步骤 3：一键生成四件套 + 前端页面 + 菜单种子（5 min）

```bash
python manage.py generate_crud crm.Customer --dry-run   # 先预览
python manage.py generate_crud crm.Customer             # 落盘
```

生成物（与 dry-run 输出一一对照）：

| 产物 | 路径 |
|------|------|
| 序列化器 | `crm/serializers.py`（单文件 + 生成块幂等合并；非 `demo` 那种 `serializers/` 包形态） |
| 视图（生成块合并） | `crm/views.py` |
| 路由 | `crm/urls.py` |
| 应用配置 | `crm/config.py`（随 `XADMIN_APPS` 自动注入路由） |
| 前端页面 | `xadmin-client/src/views/crm/customer/index.vue` + `utils/{api.ts,hook.tsx}` |
| 菜单种子 | `loadjson/seed_crm_customer.json`（菜单 + 全部权限点，pk 由 uuid5 派生，可重复装载） |

补充参数：`--with-import-export`（加导入导出）、`--component CrmCustomer`（自定义组件名）、
`--parent <菜单pk>`（挂到指定目录）、`--with-module`（顺带生成 `crm/modules.py` 可裁剪模块声明）、
`--skip-frontend`；重复执行是幂等的。

生成器输出尾部的「后续步骤」清单即下面步骤 4~5 的可复制命令，照着执行即可；
另请务必复核两点：关联字段 `input_type` 是否符合数据量；菜单是否要挂到已有目录。

## 步骤 4：装载菜单种子 + 角色授权（7 min）

```bash
python manage.py loaddata loadjson/seed_crm_customer.json
```

然后在管理后台完成三步（超管操作）：

1. **菜单管理**：把「客户」菜单挂到目标目录下（种子默认顶级），设置图标与排序；
2. **角色管理**：给相关角色勾选新增的 `*:CustomerViewSet` 权限点（字段权限按需勾选）；
3. **用户管理**：把该角色分配给使用者。

> 为什么必须灌库：前端菜单与按钮可见性取自后端权限点（`hasAuth("list:CustomerViewSet")`），
> 库里没有 = 整页不渲染/403。

## 步骤 5：验收与自检（5 min）

```bash
python manage.py doctor
```

期望看到「权限点：代码路由与库内权限点一致」；若提示缺口：

```bash
python manage.py sync_menu_permissions          # 补齐（幂等）
python manage.py sync_menu_permissions --update-seed   # 同时回写 loadjson 种子
```

前端 `pnpm dev` → 从菜单进入「客户」→ 验证新增 / 编辑 / 搜索 / 删除。

可选进阶：

- 把模块声明为「可裁剪」（config.yml 一键关闭）：生成时加 `--with-module`，或
  `python manage.py generate_module crm --app crm --level optional --route ^/api/crm/`；
- 补一条 E2E：参考 `xadmin-client/e2e/README.md` 的既有用例模式；
- 字段权限/数据权限：角色页勾选字段白名单、数据权限规则（见 `docs/architecture/permission.md`）。

## 常见问题

| 症状 | 原因与处理 |
|------|-----------|
| 菜单里看不到新模块 | 种子未装载或未挂上级目录；菜单管理核对 |
| 非超管 403 / 整页不渲染 | 权限点未授权（角色管理）或未入库（`sync_menu_permissions`）；先跑 `doctor` |
| 列表有数据但单元格空白 | 序列化器 `table_fields` 未声明该字段（元数据驱动，前端没有本地列定义） |
| 新增字段前端不显示 | 序列化器 `fields` / `table_fields` 未加；再跑 `sync_model_field` 同步字段权限树 |
| 接口 404 | app 未注册进 `XADMIN_APPS`（改后需重启进程：`docker compose restart server celery-worker celery-heavy celery-beat`） |
| 改代码不生效 | 挂载代码不热加载，重启对应容器即可 |

## 下一步阅读

- [framework-cookbook.md](../architecture/framework-cookbook.md)：ViewSet 选型、Action 覆写点、前端契约
- [模块化与功能裁剪.md](../architecture/模块化与功能裁剪.md)：把模块变成可裁剪功能项
- [dev-pitfalls.md](../dev-pitfalls.md)：新手陷阱清单（元数据 / 权限码 / 渲染器注册等）
- [plans/二次开发友好化改造方案-2026.09.md](../plans/二次开发友好化改造方案-2026.09.md)：本教程背后的改造台账
