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

## 二点五、种子该放哪：决策树

种子双轨（静态 `<app>/seeds/` 与运行期命令播种）并存带来的第一个问题就是「我的新模块种子放哪」。
按下面的决策树走（判断顺序自上而下，命中即停）：

```text
你的数据是产品功能的一部分（菜单 / 权限点 / 字段树 / 应用配置 / 字典）？
├─ 是 → 静态种子：<app>/seeds/ 三件套（manifest.json + menu.json + menumeta.json）
│        · 内置业务 app 与二开插件同口径（demo 插件即活样本）
│        · `sync_menu_permissions --update-seed` 会按归属回写，改完 `--dry-run` 验证缺口 0
│        · 平台级目录 / 跨域页面才落 loadjson/（平台共享层）
└─ 否（演示 / 可选样例 / 开箱体验数据）？
    └─ 运行期命令播种：写 seed_<名称> management command
         · demo app 的 seed_demo_book 是活样本（菜单与权限点由命令播种，随模块可裁剪）
         · 命令需幂等（唯一名 + 存在即跳过），生产默认不执行
```

判定口诀：**装完系统就该有的 → 静态种子；管理员可选开启的 → 命令播种**。
两轨都经 `load_init_json --check`（0 error）与权限点缺口扫描把关。

## 二点六、代码生成：CLI 与 GUI 同源

`generate_crud`（CLI）与「系统管理 → 代码生成」（GUI 页）**不是两套实现**：
GUI 适配层 `devtools/services/codegen/gui.py` 直接实例化 CLI 命令类
（`from devtools.management.commands._generate_crud import Command`），
预览 / 下载产物与 CLI 落盘走同一引擎与同一套模板——GUI 是 CLI 的参数化视图，
不存在「双入口产物漂移」的面。改动生成逻辑只改 `_generate_crud/`（唯一事实源）。

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
| `config.py::URLPATTERNS` | 路由前缀声明 | `server/urls.py` → `common/core/url_inventory.py::auto_register_app_url`（同时并入 `PERMISSION_SHOW_PREFIX` / `PERMISSION_DATA_AUTH_APPS`） |
| `routing.py::urlpatterns` | WS 通道 | `server/asgi.py` → `collect_app_ws_urls`（按 INSTALLED_APPS 收集） |
| `tasks.py` | 周期任务声明 | `{app}.tasks` 被 celery autodiscover；`@register_as_period_task(module=...)` 声明归属模块 |
| `modules.py::MODULES` | 功能模块声明 | `common/core/modules/registry.py::discovered_modules` |
| `ai_declarations.py::AI_ACTIONS` | AI 动作声明（白名单，随应用安装自动发现） | `ai/services/actions/ai_registry.py::action_specs`（工具目录 / MCP / function calling 同源） |
| `migrations/` | 自有数据面 | `manage.py migrate <app>`（与宿主迁移链解耦） |

三条实现要点：

1. **路由前缀 = 模块拦截面**：`modules.py` 未写 `routes` 时按 `URLPATTERNS` 静态前缀
   推导（单一事实源）；写了则显式声明优先，且两者不一致会告警（声明与真实路由漂移）。
2. **WS 通道必须显式声明 `ws_routes`**：WS 准入是「声明即拦截」的 fail-closed 语义，
   不按 `routing.py` 推导——不声明就没有裁剪层，通道会在模块停用后仍可连接。
3. **消费者自行鉴权**：内核 ASGI 栈注入 `scope["user"]`，准入判断在消费者一侧
   （未登录 `close(4401)`，与内核既有口径一致）。

### 4.1 AI 动作声明（让插件能力进入 AI 工具目录）

`ai_declarations.py` 与 `config.py` 同处于应用根，声明**只做数据**（不查库、不 import
业务模型），执行一律复用业务接口：

```python
# my_plugin/ai_declarations.py
from ai.services.actions.ai_api_actions import api_action

AI_ACTIONS = {
    "myplugin.item.list": api_action(
        key="myplugin.item.list",
        label="查询条目",  # 支持 gettext_lazy
        description="列出插件的条目（关键字过滤）",
        method="GET",
        path="/api/my-plugin/items",
        params={"search": {"type": "string", "in": "query", "description": "关键字"}},
        modules=("my_plugin",),  # 可选：随功能模块裁剪
    ),
}
```

发现器遍历已安装应用并合并声明，key 全局唯一（重复直接启动失败）；声明文件不存在
即跳过，不影响内核。新增写动作前先确认审批口径（`requires_approval`）与字段权限。

### 4.2 5 分钟四类扩展

四类扩展都有可直接复制的样板：照「复制 → 改 3 处 → 验证」做完即可，机制细节沿链接回看。

#### ① 自定义 action + 权限点

| 步骤 | 做什么 |
| --- | --- |
| 复制 | `demo/views.py::push`（`@action` 方法 + 审计写入的最小样板） |
| 改 3 处 | ① 方法名与 `url_path` 改成业务语义；② docstring 首行写成 ≤24 字名词短语（会被取作菜单标题，见 `scripts/check_menu_titles.py`）；③ 在 `<app>/seeds/menu.json` 登记权限点（`name: push:BookPush`、`path: api/demo/book/{pk}/push$`、`method: POST`），并在 `<app>/seeds/menumeta.json` 补标题 |
| 验证 | `python manage.py sync_menu_permissions --update-seed` 后 `python manage.py sync_menu_permissions --dry-run`（缺口 0 条）；再跑 `uv run pytest tests/integration/demo/test_book_viewset.py -q` |

未入库的权限点 = 非超管恒 403，登记这一步不能省。

#### ② 自定义 `input_type` 渲染器

| 步骤 | 做什么 |
| --- | --- |
| 复制 | `xadmin-client/src/components/RePlusPage/src/utils/renderers-detail.tsx` 中同类渲染器（按搜索 / 表单 / 详情通道选） |
| 改 3 处 | ① 用 `registerSearchRenderer` / `registerFormRenderer` / `registerDetailRenderer` 在模块顶层注册（放在 `xadmin-client/src/extensions/` 作为构建期扩展）；② 在 `xadmin-client/src/components/RePlusPage/src/utils/__tests__/renderers-pairing.spec.ts` 的分类清单登记新 type（四通道配对守护，未登记即失败）；③ 后端若引入新字段类，在 `packages/xadmin-common/common/drf/metadata.py::get_field_type` 加 isinstance 分支，契约变更后跑 `pnpm sync:contract` |
| 验证 | `pnpm test:run -- src/components/RePlusPage/src/utils/__tests__/renderers-pairing.spec.ts` + `pnpm typecheck`；四通道细节见 [recipes R10](recipes.md) |

#### ③ 通知渠道（宿主级扩展）

| 步骤 | 做什么 |
| --- | --- |
| 复制 | `notifications/backends/email.py` → `notifications/backends/<name>.py`，模块级保留 `backend = YourBackend` |
| 改 3 处 | ① `notifications/backends/specs.py::CHANNEL_SPECS` 增一条 `ChannelSpec`（`code` / `module` / `renderer` / `html`）；② 在 `notifications/notifications.py::Message` 补 `get_<name>_msg` 渲染方法（`get_email_msg` / `get_text_msg` 二选一）；③ 在 `notifications/backends/__init__.py::BACKEND` 枚举加值——**这是宿主级扩展，目前仍需改枚举**，否则渠道无法被订阅与选择 |
| 验证 | `uv run pytest tests/unit/notifications/test_channel_specs.py tests/unit/notifications/test_backend_loading.py -q`（缺 spec 或 client 会被守护拦下） |

#### ⑤ 匿名写接口挂人机校验（`require_human`）

匿名可达且有副作用的接口必须过人机校验，统一入口：

```python
from identity.utils.auth.captcha import require_human


class YourAnonymousAPIView(GenericAPIView):
    def post(self, request, *args, **kwargs):
        # scene 取 captcha.const 的已登记场景；action 必须在该 scene 的动作表内
        require_human(request, "login", "your.action", need=True)
        ...
```

- 业务请求不携带 `action`：消费点用服务端常量（`captcha.const`）声明；
- 新场景/动作先扩 `CAPTCHA_SCENES` / `CAPTCHA_SCENE_ACTIONS`，再在
  `<app>/declarations.py` 登记权限点归属；
- 门禁：`python scripts/check_captcha_coverage.py` 会扫描全部匿名写端点；确有上游
  人机校验等例外时，在脚本 `EXEMPTIONS` 写明理由（并在 `docs/ci-gates.md` 登记变更）。

#### ④ 审批 biz sync（终态回写业务单）

| 步骤 | 做什么 |
| --- | --- |
| 复制 | `demo/services.py::sync_book_instance` → `<app>/services.py` |
| 改 3 处 | ① `<app>/config.py::APPROVAL_BIZ_SYNCERS` 增 `{"<biz_type>": "<app>.services.sync_xxx"}`（注册表按声明收集，零核心文件改动）；② 提交端 `create_instance(..., biz_type=..., biz_id=...)` 绑定业务单；③ 回写函数按 APPROVED / REJECTED / CANCELLED 幂等映射业务状态，只写状态字段、不动 `modifier` |
| 验证 | `uv run pytest tests/integration/demo/test_book_flow.py -q`；或前端走一遍「提交 → 审批通过 → 业务状态回写」 |

跨 app 调用引擎一律函数内惰性导入（跨 app import 门禁），样板已按此组织。

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

## 八、前端页面配套（`xadmin-client/src/extensions/`）

插件的后端接口就绪后，前端页面落在宿主前端仓的 `src/extensions/<module>/`
（不污染 `src/views/`；该目录已被动态路由 glob 覆盖，**无需改前端路由文件**）。
活样本：`src/extensions/demo-plugin/` + 本插件种子，最小三件——

1. 页面组件 `extensions/<module>/index.vue`：`defineOptions({ name })` 是路由
   匹配键，必须与菜单种子的 `name`、权限点 `<动作>:<组件名>` 后缀一致；
   列表页用 `RePlusPage`，列 / 表单 / 按钮可全部来自插件后端的
   `search-columns` / `search-fields` 元数据（后端加字段 → 前端免改）。
2. 插件种子 `xadmin_demo_plugin/seeds/menu.json`：页面菜单（`menu_type=1`、
   `component="extensions/<module>/index"`）+ 权限点（`menu_type=2`）；宿主把
   插件写进 `XADMIN_APPS` 后 `load_init_json` 自动收集，未装插件则不残留菜单。
3. 断链守护：`xadmin-client` 的 `pnpm check:menu-components` 会校验插件种子
   引用的页面文件真实存在（含 `examples/plugins/*/*/seeds/`）——插件改组件名
   / 移页面而种子未同步，CI 直接拦截。

接线细节与约定见 `xadmin-client/src/extensions/README.md`。

## 九、参考

- 契约面与白名单：`packages/xadmin-common/common/contracts.py`
- 模块声明 / 解析 / 裁剪：`packages/xadmin-common/common/core/modules/`
- 装配链：`server/settings/apps.py`、`common/apps.py`、`server/urls.py`、`server/asgi.py`
- 可运行样本与守卫测试：`examples/plugins/xadmin-demo-plugin`、`tests/integration/plugins/test_plugin_demo.py`
- 相关文档：[二开速查](secondary-dev-quickref.md)、[模块化与功能裁剪](../architecture/模块化与功能裁剪.md)、
  [内核发包与宿主接线](../architecture/kernel-package.md)
