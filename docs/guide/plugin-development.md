# 二开插件实战：契约注入 / 模块声明 / 发行预设

> 面向**不改宿主源码**的二次开发：把能力做成分发包装进来即用，随包携带路由、WS 通道、
> 周期任务、自有表与模块声明。可运行样本：
> [`examples/plugins/xadmin-demo-plugin`](../../examples/plugins/xadmin-demo-plugin/README.md)（本仓库内，含守卫测试）。
>
> 与相关文档的分工：
> [二开速查](secondary-dev-quickref.md) 是场景 → 权威入口的索引页；
> [模块化与功能裁剪](../architecture/模块化与功能裁剪.md) 讲内核侧机制；
> 本文只讲「插件作者要做什么、边界在哪、怎么验证」。

## 一、三条接入通道（先看全貌）

| 通道 | 载体 | 装配时机 | 典型用途 |
| --- | --- | --- | --- |
| 契约注入 · entry point | `pyproject.toml` 的 `[project.entry-points."xadmin.contracts"]` | `common.ready()`（全部 app ready 之后、URLConf 之前） | 装包即生效的通用覆盖；宿主零接线 |
| 契约注入 · `AppConfig.ready()` | 插件 `apps.py` | 该 app ready 时（早于 `common.ready()`） | 需要在注入前读宿主配置 / 精确控制时序 |
| 应用级扩展点 | `config.py` / `routing.py` / `tasks.py` / `modules.py` / `migrations/` | 随 app 装配链 | 路由、WS 通道、周期任务、模块声明、自有表 |

两条注入路径**按契约二选一**：同一契约双路径注入会被 `register_contract` 以
`ValueError: contract ... already has an injected provider` 在启动期拒绝（防二开包互踩）。

## 二、快速开始（以示例插件为模板）

```bash
# 1. 复制骨架（或按本文章节自建）
cp -r examples/plugins/xadmin-demo-plugin /path/to/my-plugin

# 2. 装包（写入 entry point 元数据；开发态用 -e 可实时改）
pip install /path/to/my-plugin

# 3. 注册进宿主 config.yml：路由 / 模块声明 / WS 通道随之生效
#    XADMIN_APPS:
#      - demo
#      - my_plugin

# 4. 建插件自有表（插件自带迁移，与宿主迁移链解耦）
python manage.py migrate my_plugin

# 5. 菜单与权限点按宿主既有流程入库（种子或后台配置），重启进程
python manage.py sync_menu_permissions --update-seed
```

包装完必须能回答三个问题，否则不算接完：**契约有没有生效**（§三）、**路由/通道/任务有没有接线**
（§四）、**模块开关能不能一起管住它们**（§五）。§六 给了逐条验证命令。

## 三、契约注入（`register_contract`）

### 3.1 白名单与解析序

契约面唯一入口是 `packages/xadmin-common/common/contracts.py`：`_CONTRACT_PROVIDERS`
是「契约名 → (提供方模块, 原因)」的**声明式白名单**，解析序为

```
注入覆盖（register_contract / entry point） → 白名单默认提供方 → 未声明名 AttributeError
```

三条硬约束：

- **白名单外名字不可注入**：新能力要先进 `_CONTRACT_PROVIDERS` 声明（连同
  `scripts/check_cross_app_imports.py` 的 `CONTRACT_SEAMS` 同步），注入不会扩大缝面；
- **重复注册 fail-fast**：替换实现须先 `unregister_contract` 显式表达意图；
- **解析时机**：`contracts.xxx` 属性访问**调用期**解析（注册后即时生效）；
  `from common.contracts import xxx` 在**消费方模块 import 期**绑定——注入必须早于该 import。
  两条装配点（entry point / `ready()`）都在 `common.ready()` 之前，满足这一边界。

### 3.2 entry point 路径（推荐：宿主零接线）

```toml
# pyproject.toml
[project.entry-points."xadmin.contracts"]
get_active_superuser_queryset = "my_plugin.providers:get_active_superuser_queryset"
```

条目名 = 契约名（须在白名单内），条目值 = 提供方对象 `pkg.mod:attr`。
`common/apps.py::ready()` 调 `load_contract_entry_points()` 逐个 `register_contract`；
加载失败或名字白名单外一律启动期报错（不静默降级）。

### 3.3 `AppConfig.ready()` 路径（需要时序或条件判断时）

```python
class MyPluginConfig(AppConfig):
    name = "my_plugin"

    def ready(self) -> None:
        from common.contracts import register_contract

        from my_plugin.providers import guarded_models

        register_contract("guarded_models", guarded_models)
```

`ready()` 早于 `common.ready()`，因此**先于** entry point 装配；两者写同一注册表，
只是装配点不同。

### 3.4 提供方实现的写法与边界

```python
def get_active_superuser_queryset():
    # 取默认实现：直接 import 提供方模块，不要经 common.contracts（会解析到自己 → 递归）
    from identity.services import get_active_superuser_queryset as default_provider

    queryset = default_provider()
    allowlist = list(getattr(settings, "MY_PLUGIN_ALERT_ALLOWLIST", []) or [])
    return queryset.filter(username__in=allowlist) if allowlist else queryset
```

- **签名与内核默认一致**：覆盖是「换实现」不是「换接口」——消费方的调用点不会改；
- **越少越好**：覆盖 `SystemConfig` / `Menu` / 数据权限类契约会改变全局语义，只在确有需要时做；
- **窄面优先**：能通过应用级扩展点或配置项达成的效果，不要走契约覆盖（覆盖是全局开关）。

## 四、应用级扩展点（不改宿主工程文件）

| 文件 | 契约 | 宿主消费点 |
| --- | --- | --- |
| `config.py::URLPATTERNS` | 路由前缀声明 | `server/urls.py` → `common/core/utils.py::auto_register_app_url`（同时并入 `PERMISSION_SHOW_PREFIX` / `PERMISSION_DATA_AUTH_APPS`） |
| `routing.py::urlpatterns` | WS 通道 | `server/asgi.py` → `collect_app_ws_urls`（按 INSTALLED_APPS 收集） |
| `tasks.py` | 周期任务声明 | `{app}.tasks` 被 celery autodiscover；`@register_as_period_task(module=...)` 声明归属模块 |
| `modules.py::MODULES` | 功能模块声明 | `common/core/modules/registry.py::discovered_modules` |
| `migrations/` | 自有数据面 | `manage.py migrate <app>`（与宿主迁移链解耦） |

三条实现要点：

1. **路由前缀 = 模块拦截面**：`modules.py` 未写 `routes` 时按 `URLPATTERNS` 静态前缀
   推导（单一事实源）；写了则显式声明优先，且两者不一致会告警（声明与真实路由漂移）。
2. **WS 通道必须显式声明 `ws_routes`**：WS 准入是「声明即拦截」的 fail-closed 语义，
   不按 `routing.py` 推导——不声明就没有裁剪层，通道会在模块停用后仍可连接。
3. **消费者自行鉴权**：内核 ASGI 栈注入 `scope["user"]`，准入判断在消费者一侧
   （未登录 `close(4401)`，与内核既有口径一致）。

## 五、功能模块声明与发行预设

```python
# my_plugin/modules.py
from common.core.modules import OPTIONAL, ModuleSpec

MODULES = (
    ModuleSpec(
        "my_plugin",
        "我的插件",
        OPTIONAL,
        menus=("MyPlugin",),  # 菜单根 name（含全部后代与权限点）
        routes=(r"^/api/my-plugin/",),  # 未写则按 config.py::URLPATTERNS 推导
        ws_routes=(r"^/ws/my-plugin/",),  # 显式声明，否则无 WS 裁剪层
        depends=("chat",),  # 依赖模块：未开启时启动期 fail-fast
    ),
)
```

### 5.1 发行预设语义

| 预设 | 包含等级 | 效果 |
| --- | --- | --- |
| `core` | `core` | 仅内核模块（`core` 级不可关闭，写入 `MODULE_DISABLE` 会报错） |
| `standard` | `core` + `standard` | 常规发行版 |
| `full` | `core` + `standard` + `optional` | 全功能版（**插件通常声明 `optional`**） |

`MODULE_ENABLE` / `MODULE_DISABLE` 在预设之上做显式覆盖；
`config.yml` 片段可由 `python manage.py module --config` 生成，模块清单用 `python manage.py module --list` 核对。

### 5.2 停用即六层裁剪（声明齐全时自动生效）

| 层 | 生效点 | 插件作者要做的事 |
| --- | --- | --- |
| ① 请求路由 | `ModuleGateMiddleware` → 404 + `{"code":1001,"module":"<id>"}` | 声明 `routes`（或让前缀可由 `URLPATTERNS` 推导） |
| ② WS 通道 | `ModuleTrimWebsocketMiddleware` → close 4404 | 声明 `ws_routes` |
| ③ 菜单 / 权限点 | `compute_hidden_menu_pks`（菜单子树 + 权限点前缀） | 声明 `menus`（权限点按前端登记的前缀自动覆盖） |
| ④ 周期任务 | 注册链路跳过停用模块任务并清理历史条目 | `@register_as_period_task(module="<id>")` |
| ⑤ 种子导入 | `ModuleSeedFilter` 按模块过滤 | 种子数据挂到该模块的菜单/权限点下 |
| ⑥ 缓存失效 | `invalidate_trimmed_caches` | 无需额外动作（首次解析自动清理） |

语义红线：**停用只隐藏与拦截，不删业务数据**。重启进程后配置生效（模块组合在启动期解析）。

## 六、验证与自检

```bash
# ① 契约注入是否生效（`__module__` 即证据）
python -c "import django;django.setup();import common.contracts as c;print(c.get_active_superuser_queryset.__module__)"

# ② 模块声明是否进清单（等级 / 依赖 / 是否启用）
python manage.py module --list

# ③ 路由是否接上（前缀 + 视图类）
python -c "import django;django.setup();from django.urls import resolve;print(resolve('/api/my-plugin/note').func.cls.__name__)"

# ④ 六层裁剪：config.yml 写入 MODULE_DISABLE: [my_plugin] 并重启，然后
curl -i http://127.0.0.1:8896/api/my-plugin/note    # 路由层 404 + module 标识
python manage.py doctor                             # 启动自检（含权限点缺口扫描）
```

仓库侧守护：插件进 `examples/` 后由
`tests/integration/plugins/test_plugin_demo.py`（三通道装配 + 预设语义 + 六层拦截）
与 `scripts/check_doc_paths.py`（文档路径可达）覆盖；示例插件的安装级验证步骤见其 README。

## 七、常见坑

| 现象 | 根因 | 处置 |
| --- | --- | --- |
| 启动即 `ValueError: contract ... already has an injected provider` | 同一契约被 entry point 与 `ready()` 双路径注入 | 按契约二选一 |
| 契约覆盖「没生效」 | `from common.contracts import xxx` 在消费方 import 期已绑定默认实现 | 让注入早于该 import（两条装配点天然满足），或改用属性访问式消费点 |
| 停用模块后接口仍可访问 | `modules.py` 未声明，或 `routes` 前缀与实际路由漂移（有告警） | 补声明 / 让前缀由 `URLPATTERNS` 推导 |
| WS 通道停用后仍可连 | 未声明 `ws_routes` | 补声明并重启 |
| 模型报 `isn't in an application in INSTALLED_APPS` | 插件没进 `XADMIN_APPS`（只装包不注册），或漏了 `apps.py` | 补 `XADMIN_APPS` 并重启 |
| 迁移报依赖缺失 | 插件迁移引用了宿主 app 的中间状态迁移 | 依赖写到宿主**已发布**的迁移上，或改用抽象基类字段 |
| 周期任务停用后仍在跑 | 装饰器漏写 `module=` | 补 `module="<id>"` 并重启 beat |

## 八、参考

- 契约面与白名单：`packages/xadmin-common/common/contracts.py`
- 模块声明 / 解析 / 裁剪：`packages/xadmin-common/common/core/modules/`
- 装配链：`server/settings/apps.py`、`common/apps.py`、`server/urls.py`、`server/asgi.py`
- 可运行样本与守卫测试：`examples/plugins/xadmin-demo-plugin`、`tests/integration/plugins/test_plugin_demo.py`
- 相关文档：[二开速查](secondary-dev-quickref.md)、[模块化与功能裁剪](../architecture/模块化与功能裁剪.md)、
  [内核发包与宿主接线](../architecture/kernel-package.md)
