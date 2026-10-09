# xadmin 二开插件示例（xadmin-demo-plugin）

一个**可独立分发**的插件包骨架，演示 xadmin 二开的全部接入通道，并作为
[`docs/guide/plugin-development.md`](../../../docs/guide/plugin-development.md) 教程的可运行样本。

| 通道 | 载体 | 本插件用它做什么 |
| --- | --- | --- |
| 契约注入 · entry point | `pyproject.toml` 的 `[project.entry-points."xadmin.contracts"]` | 覆盖 `get_active_superuser_queryset`（告警收件人白名单） |
| 契约注入 · `AppConfig.ready()` | `xadmin_demo_plugin/apps.py` | 覆盖 `guarded_models`（插件模型纳入删除影响面守卫） |
| 路由自动注入 | `xadmin_demo_plugin/config.py::URLPATTERNS` | `/api/plugin-demo/` → 便签 CRUD |
| 功能模块声明 | `xadmin_demo_plugin/modules.py` | 模块 id `demo_plugin`（`optional`，参与发行预设与六层裁剪） |
| WebSocket 通道 | `xadmin_demo_plugin/routing.py` + `consumers.py` | `ws/plugin-demo/` 就绪回执（模块停用即 4404） |
| 周期任务 | `xadmin_demo_plugin/tasks.py` | 每日 04:30 统计（模块停用不注册） |
| 自有数据面 | `models.py` + `migrations/` | 插件表 `PluginNote`（宿主只跑本插件迁移） |

## 安装与启用

在宿主工程 `xadmin-server` 目录内执行：

```bash
# 1. 装包（同时写入 entry point 元数据）
pip install ./examples/plugins/xadmin-demo-plugin
# 或开发态：uv pip install -e ./examples/plugins/xadmin-demo-plugin

# 2. 注册进宿主（config.yml）：路由 / 模块声明 / WS 通道随之生效
#    XADMIN_APPS:
#      - demo
#      - xadmin_demo_plugin

# 3. 建插件表（插件自带迁移，与宿主迁移链解耦）
python manage.py migrate xadmin_demo_plugin

# 4. 重启进程（装配与模块组合在启动期解析；celery worker/beat 同重启）
```

菜单与权限点：插件自己的菜单/权限点按宿主既有流程入库
（`python manage.py sync_menu_permissions --update-seed` 或直接在后台配置）。
模块清单可随时用 `python manage.py module --list` 核对 `demo_plugin` 的等级与开关状态。

## 验证清单（装完即可对照执行）

```bash
# ① 契约注入生效：告警收件人被收敛到插件白名单（空配置 = 与内核默认一致）
python - <<'PY'
import django; django.setup()
import common.contracts as contracts
print(contracts.get_active_superuser_queryset.__module__)  # → xadmin_demo_plugin.providers
PY

# ② 模块声明进入清单（发行预设语义：optional 仅 full 预设默认开启）
python manage.py module --list | grep demo_plugin

# ③ 六层裁剪：停用模块后逐层验证
#    config.yml 写入 MODULE_DISABLE: [demo_plugin] 并重启，然后：
curl -i http://127.0.0.1:8896/api/plugin-demo/note     # ④ 路由层：404 + {"code":1001,"module":"demo_plugin"}
#    ⑤ WS 层：ws/plugin-demo/ 被拒（close 4404）
#    ⑥ 菜单/权限点：DemoPlugin 菜单子树与该前缀权限点对非超管不可见
#    ⑦ 周期任务：python manage.py module --list 中该任务不再注册
#    ⑧ 种子导入 / 缓存：见 docs/guide/plugin-development.md §六
```

## 注意事项

- **同一契约不可双路径注入**：`register_contract` 对重复注册 fail-fast
  （`ValueError: contract ... already has an injected provider`）。本插件按契约拆分了两条路径；
  复制本样例时请二选一。
- **契约名必须在白名单内**：新增契约要先在 `packages/xadmin-common/common/contracts.py`
  的 `_CONTRACT_PROVIDERS` 声明（连同步 `scripts/check_cross_app_imports.py` 的 `CONTRACT_SEAMS`），
  白名单外名字注入会被启动期拒绝。
- **注入时序**：entry point 与 `AppConfig.ready()` 都在 `common.ready()` 之前完成装配；
  `from common.contracts import xxx` 形态在消费方 import 期绑定，属性访问式消费点
  （`contracts.xxx(...)`）调用期解析、注册后即时生效。
- **停用 ≠ 卸载**：模块停用只做「隐藏 + 拦截」（请求 404 / WS 4404 / 菜单隐藏 / 任务不注册），
  不删插件数据；彻底移除再走 `pip uninstall` + 宿主 `XADMIN_APPS` 摘除。

## 卸载与回滚

```bash
# 1. 先从 config.yml 的 XADMIN_APPS 摘除插件并重启（路由/模块/通道即时下线）
# 2. 数据面按需保留：插件表与迁移记录不动，回滚后重新启用即可继续用
pip uninstall xadmin-demo-plugin
```
