# -*- coding: utf-8 -*-
"""功能模块裁剪矩阵演练（F1）：六层裁剪的运行期证据。

对应长期优化方案 §5.5 F1「裁剪矩阵演练」。六层与证据：

1. **请求路由**：停用模块 REST 路径经 ModuleGateMiddleware 直接 404（含业务码）；
2. **WS 通道**：停用模块 `ws_routes` 声明的 WebSocket 通道在准入层拒绝
   （ModuleTrimWebsocketMiddleware，close 4404），内核通道不受影响；
3. **菜单/权限点**：`compute_hidden_menu_pks` 双口径（菜单子树 + 权限点前缀），
   本文件用 loadjson 种子行验证（运行期与种子导入同源，见 test_module_seed.py）；
4. **周期任务**：`create_or_update_registered_periodic_tasks` 跳过停用模块任务
   并清理历史注册条目；
5. **种子导入**：tests/unit/system/test_module_seed.py（过滤口径与运行期同源）；
6. **缓存失效**：`invalidate_trimmed_caches` 清理菜单路由 / 权限码缓存。

演练样本固定 `chat`：具备路由 / 菜单 / 权限点 / 周期任务 / 种子 / WS 六类声明，
是覆盖面最完整的模块之一。`TestThirdPartyDeclarationPath` 用 app 侧声明（第三方
扩展点）走同一矩阵，证明第三方模块不需要特殊通路。裁剪语义红线：只隐藏与拦截、
不删除业务数据。
"""

import json
import os
import re
import sys
import types

import pytest
from asgiref.sync import async_to_sync
from django.apps import apps as django_apps
from django.conf import settings as dj_settings
from django.test import Client

from common.celery.decorator import get_register_period_tasks
from common.core.modules import (
    MENU_TYPE_DIRECTORY,
    MENU_TYPE_PERMISSION,
    OPTIONAL,
    ModuleSeedFilter,
    ModuleSpec,
    ModuleTrimWebsocketMiddleware,
    all_module_specs,
    compute_hidden_menu_pks,
    disabled_permission_prefixes,
    invalidate_trimmed_caches,
    is_module_enabled,
    is_ws_path_trimmed,
    module_index,
    permission_prefixes_of,
)

TRIMMED = "chat"
LOADJSON_DIR = os.path.join(dj_settings.PROJECT_DIR, "loadjson")

# WS 通道归属登记表（与 message.routing / system.routing 一一对应）：
#   channel 正则 → (模块 id | None=内核通道, 该通道的真实访问样例路径)
# 新增 WS 通道时必须先在 ModuleSpec.ws_routes 声明归属（或登记为内核豁免），
# 否则下面的声明一致性测试会失败——防止新通道漏出裁剪矩阵。
WS_CHANNEL_OWNERSHIP = {
    r"ws/message/(?P<group_name>[\w+|\-?]+)+/(?P<username>\w+)$": (None, "/ws/message/xadmin/demo"),
    r"ws/chat/$": (TRIMMED, "/ws/chat/"),
    r"ws/tasks/log/(?P<pk>[0-9a-f]{32}|[0-9a-f\-]{36})$": (
        None,
        "/ws/tasks/log/0123456789abcdef0123456789abcdef",
    ),
    r"ws/system/monitor/$": ("ops", "/ws/system/monitor/"),
    r"ws/screen/(?P<pk>[0-9a-f\-]{36})$": ("analysis", "/ws/screen/5eed0001-0000-0000-0000-000000000002"),
}

pytestmark = pytest.mark.django_db


def _run_ws_middleware(path):
    """执行 WS 准入中间件：返回（下发帧, 内层 app 收到的路径）。"""

    sent = []
    inner_paths = []

    async def inner(scope, receive, send):
        inner_paths.append(scope["path"])

    async def send(message):
        sent.append(message)

    async def receive():  # pragma: no cover - 准入不读取入站帧
        return {"type": "websocket.connect"}

    async_to_sync(ModuleTrimWebsocketMiddleware(inner))({"type": "websocket", "path": path}, receive, send)
    return sent, inner_paths


def _registered_ws_regexes():
    """message/system routing 中实际注册的 WS 路径正则（唯一事实源的运行期投影）。"""

    from dataset.routing import urlpatterns as dataset_patterns
    from message.routing import urlpatterns as message_patterns
    from system.routing import urlpatterns as system_patterns

    return [pattern.pattern.regex.pattern for pattern in (*message_patterns, *system_patterns, *dataset_patterns)]


@pytest.fixture(scope="module", autouse=True)
def _autodiscover_tasks():
    """与 celery 启动同路径导入各 app 的 tasks 模块。

    生产由 `server/celery.py` 的 `app.autodiscover_tasks()` 完成；测试进程不初始化
    celery app，需显式触发，周期任务装饰器才会在导入期登记（否则本文件第二层
    周期任务断言看不到任何声明）。
    """

    from django.utils.module_loading import autodiscover_modules

    autodiscover_modules("tasks")


def _seed_menu_rows():
    """(pk, parent_id, menu_type, name, path) 形态的种子菜单行（与运行期查询同形）。"""

    with open(os.path.join(LOADJSON_DIR, "menu.json"), encoding="utf-8") as fp:
        data = json.load(fp)
    rows = []
    for item in data:
        fields = item["fields"]
        rows.append(
            (
                item["pk"],
                fields.get("parent"),
                fields.get("menu_type"),
                fields.get("name"),
                fields.get("path"),
            )
        )
    return rows


class TestRouteLayer:
    def test_disabled_module_rest_returns_404(self, module_config):
        module_config(disable=[TRIMMED])
        response = Client().get("/api/chat/room")
        assert response.status_code == 404
        assert response.json()["code"] == 1001

    def test_core_path_not_gated(self, module_config):
        module_config(disable=[TRIMMED])
        # 内核路径不受模块网关影响（未登录应为 401/403，而非模块 404）
        assert Client().get("/api/system/user").status_code != 404


class TestWebsocketChannelLayer:
    def test_disabled_module_ws_channel_rejected_before_auth(self, module_config):
        """停用 chat 后 `/ws/chat/` 在准入层拒绝（close 4404），不进入认证/consumer。"""

        module_config(disable=[TRIMMED])
        sent, inner = _run_ws_middleware("/ws/chat/")
        assert sent == [{"type": "websocket.close", "code": 4404}]
        assert inner == []

    def test_core_channels_unaffected_by_trim(self, module_config):
        """内核通道（通知推送 / 任务日志）无 ws_routes 声明，不受裁剪影响。"""

        module_config(disable=[TRIMMED])
        for path in ("/ws/message/xadmin/demo", "/ws/tasks/log/0123456789abcdef0123456789abcdef"):
            sent, inner = _run_ws_middleware(path)
            assert sent == []
            assert inner == [path]

    def test_reenabled_module_channel_recovers(self, module_config):
        module_config(disable=[TRIMMED])
        module_config()
        sent, inner = _run_ws_middleware("/ws/chat/")
        assert sent == []
        assert inner == ["/ws/chat/"]

    def test_media_channel_trimmed_with_analysis(self, module_config):
        module_config(disable=["analysis"])
        sent, _inner = _run_ws_middleware("/ws/screen/5eed0001-0000-0000-0000-000000000002")
        assert sent == [{"type": "websocket.close", "code": 4404}]


class TestMenuAndPermissionLayer:
    def test_chat_menu_subtree_hidden_by_seed_rows(self, module_config):
        module_config(disable=[TRIMMED])
        spec = module_index()[TRIMMED]
        assert spec.menus, "chat 需声明菜单根（演练前提）"

        rows = _seed_menu_rows()
        hidden = compute_hidden_menu_pks(rows, names=spec.menus, prefixes=permission_prefixes_of([spec]))
        hidden_names = {row[3] for row in rows if row[0] in hidden}
        assert set(spec.menus) & hidden_names, (
            f"声明菜单根应进入隐藏集合：声明={spec.menus} 隐藏={sorted(hidden_names)[:5]}"
        )

    def test_permission_prefixes_cover_chat_rest_paths(self, module_config):
        module_config(disable=[TRIMMED])
        prefixes = disabled_permission_prefixes()
        # chat 权限点 path 形如 api/chat/room$：路由前缀派生前缀，不依赖菜单挂载位置
        assert any(prefix.startswith("api/chat/") for prefix in prefixes)


class TestPeriodicTaskLayer:
    def test_disabled_module_tasks_skipped_and_cleaned(self, module_config):
        from django_celery_beat.models import PeriodicTask

        from common.tasks import create_or_update_registered_periodic_tasks

        module_config(disable=[TRIMMED])
        registered = [(name, detail) for entry in get_register_period_tasks() for name, detail in entry.items()]
        chat_names = {name for name, detail in registered if detail.get("module") == TRIMMED}
        assert chat_names, "chat 需声明周期任务（演练前提）"
        assert all(not is_module_enabled(TRIMMED) for _ in chat_names)

        # 与生产启动同路径执行一次注册：停用模块任务被 skip + 历史条目清理
        create_or_update_registered_periodic_tasks()
        assert not PeriodicTask.objects.filter(name__in=chat_names).exists()

        # 对照组：全量配置下内核周期任务可正常注册（证明注册链路本身在工作）
        module_config()
        create_or_update_registered_periodic_tasks()
        core_names = {name for name, detail in registered if detail.get("module") is None}
        assert core_names and PeriodicTask.objects.filter(name__in=core_names).exists()


class TestCacheInvalidationLayer:
    def test_menu_and_permission_caches_cleared(self, module_config):
        from django.core.cache import cache

        module_config(disable=[TRIMMED])
        keys = (
            "magic_cache_data_get_user_permission_1",
            "magic_cache_response_UserRoutesAPIView_get_1",
        )
        for key in keys:
            cache.set(key, "stale", timeout=60)
        removed = invalidate_trimmed_caches()
        assert removed >= len(keys)
        assert all(cache.get(key) is None for key in keys)

    def test_noop_when_nothing_disabled(self, module_config):
        module_config()
        assert invalidate_trimmed_caches() == 0


class TestThirdPartyDeclarationPath:
    """第三方 app 声明路径验证：app 侧 modules.py 声明与内置模块六层裁剪同口径。

    扩展点契约（模块化文档 §九路径 C）：app 包内提供 modules.py，声明模块级
    ``MODULES`` 元组即随安装自动进入清单，无需改本项目源码。本节以一个含
    REST / WS / 菜单 / 周期任务声明的 optional 模块走完「发现 → 预设语义 →
    各层裁剪 → 恢复」全链路，证明第三方声明走的是与内置模块完全相同的通路。
    """

    MODULE_NAME = "third_party_app.modules"
    MODULE_ID = "third_biz"
    SAMPLE_REST = "/api/third-party/items"
    SAMPLE_WS = "/ws/third-party/room/"
    SPEC = ModuleSpec(
        MODULE_ID,
        "第三方业务",
        OPTIONAL,
        menus=("ThirdPartyRoot",),
        routes=(r"^/api/third-party/",),
        ws_routes=(r"^/ws/third-party/",),
    )

    @pytest.fixture
    def third_party_app(self, monkeypatch):
        """注册一个已安装 app 的 modules.py 声明（不改动仓库文件）。"""

        fake = types.ModuleType(self.MODULE_NAME)
        fake.MODULES = (self.SPEC,)
        monkeypatch.setitem(sys.modules, self.MODULE_NAME, fake)

        class _AppConfig:
            name = "third_party_app"
            path = os.path.dirname(__file__)  # HTTP 404 模板渲染会遍历 app 目录

        monkeypatch.setattr(django_apps, "get_app_configs", lambda: [_AppConfig()])
        yield
        # get_app_template_dirs 是进程级缓存：mock 生效期间发起 HTTP 请求（404 页渲染）
        # 会把「app 模板目录」扫描结果污染成空集，同 worker 后续测试随即
        # TemplateDoesNotExist（msg_verify_code.html 等 → 500）。测试结束强制清缓存，
        # 让后续扫描基于恢复后的真实 app 集合。
        from django.template.utils import get_app_template_dirs

        get_app_template_dirs.cache_clear()

    def test_declaration_joins_registry_and_preset_semantics(self, third_party_app, module_config):
        module_config()
        assert self.MODULE_ID in {spec.id for spec in all_module_specs()}
        assert is_module_enabled(self.MODULE_ID) is True  # optional 随 full 预设默认开启

        module_config(preset="standard")
        assert is_module_enabled(self.MODULE_ID) is False  # optional 不随 standard/core 预设开启

        module_config(preset="standard", enable=[self.MODULE_ID])
        assert is_module_enabled(self.MODULE_ID) is True  # MODULE_ENABLE 显式覆盖可开启

        module_config(disable=[self.MODULE_ID])
        assert is_module_enabled(self.MODULE_ID) is False  # MODULE_DISABLE 与内置模块同口径

    def test_rest_path_gated_when_disabled(self, third_party_app, module_config):
        module_config(disable=[self.MODULE_ID])
        response = Client().get(self.SAMPLE_REST)
        assert response.status_code == 404
        assert response.json()["code"] == 1001

        module_config()
        response = Client().get(self.SAMPLE_REST)
        assert response.status_code == 404  # 路径未实现：普通 Django 404
        # 不再是模块网关的 JSON 404（网关响应固定 application/json）
        assert not response.headers.get("Content-Type", "").startswith("application/json")

    def test_ws_channel_rejected_when_disabled_and_recovers(self, third_party_app, module_config):
        module_config(disable=[self.MODULE_ID])
        sent, inner = _run_ws_middleware(self.SAMPLE_WS)
        assert sent == [{"type": "websocket.close", "code": 4404}]
        assert inner == []

        module_config()
        sent, inner = _run_ws_middleware(self.SAMPLE_WS)
        assert sent == []
        assert inner == [self.SAMPLE_WS]

    def test_menu_subtree_hidden_when_disabled(self, third_party_app, module_config):
        rows = [
            (1001, None, MENU_TYPE_DIRECTORY, "ThirdPartyRoot", "/third-party"),
            (1002, 1001, MENU_TYPE_PERMISSION, "list:ThirdParty", "api/third-party/items$"),
            (1003, None, MENU_TYPE_PERMISSION, "list:SystemUser", "api/system/user$"),
        ]
        module_config()
        assert not compute_hidden_menu_pks(rows)  # 启用时零隐藏

        module_config(disable=[self.MODULE_ID])
        # 默认口径 = 全部停用声明（含第三方）：菜单根子树 + 路由前缀派生的权限点行
        hidden = compute_hidden_menu_pks(rows)
        assert {1001, 1002} <= hidden
        assert 1003 not in hidden  # 内核权限点不受影响

    def test_seed_filter_excludes_third_party_rows(self, third_party_app, module_config):
        module_config(disable=[self.MODULE_ID])
        seed_filter = ModuleSeedFilter.build()  # 停用声明（含第三方）共同构造
        assert seed_filter is not None

        menu_rows = [
            {
                "pk": "third_menu",
                "model": "system.menu",
                "fields": {
                    "parent": None,
                    "menu_type": MENU_TYPE_DIRECTORY,
                    "name": "ThirdPartyRoot",
                    "path": "/third-party",
                    "meta": "third_meta",
                },
            },
            {
                "pk": "core_menu",
                "model": "system.menu",
                "fields": {
                    "parent": None,
                    "menu_type": 1,
                    "name": "SystemUser",
                    "path": "/system/user/index",
                    "meta": "core_meta",
                },
            },
        ]
        assert [row["pk"] for row in seed_filter.filter_rows("system.menu", menu_rows)] == ["core_menu"]

        meta_rows = [
            {"pk": "third_meta", "model": "system.menumeta", "fields": {}},
            {"pk": "core_meta", "model": "system.menumeta", "fields": {}},
        ]
        # 仅被剔除菜单引用的 meta 一并剔除（孤儿 meta 保留）
        assert [row["pk"] for row in seed_filter.filter_rows("system.menumeta", meta_rows)] == ["core_meta"]

        field_rows = [
            {"pk": "fp_third", "model": "system.fieldpermission", "fields": {"menu": "third_menu"}},
            {"pk": "fp_core", "model": "system.fieldpermission", "fields": {"menu": "core_menu"}},
        ]
        assert [row["pk"] for row in seed_filter.filter_rows("system.fieldpermission", field_rows)] == ["fp_core"]

    def test_periodic_task_of_disabled_third_party_module_skipped(self, third_party_app, module_config, monkeypatch):
        from django_celery_beat.models import PeriodicTask

        from common.tasks import create_or_update_registered_periodic_tasks

        task_detail = {
            "third_biz_sync_job": {
                "task": "third_party_app.tasks.sync_job",
                "interval": 3600,
                "crontab": None,
                "args": (),
                "kwargs": {},
                "description": "第三方模块同步任务",
                "module": self.MODULE_ID,
            }
        }
        monkeypatch.setattr("common.celery.decorator.get_register_period_tasks", lambda: [task_detail])

        module_config(disable=[self.MODULE_ID])
        create_or_update_registered_periodic_tasks()
        assert not PeriodicTask.objects.filter(name="third_biz_sync_job").exists()

        module_config()  # 重新启用随注册链路自动重建（与内置模块同一行为）
        create_or_update_registered_periodic_tasks()
        assert PeriodicTask.objects.filter(name="third_biz_sync_job").exists()


class TestDeclarationConsistency:
    def test_all_declared_modules_have_unique_ids(self):
        ids = [spec.id for spec in all_module_specs()]
        assert len(ids) == len(set(ids))

    def test_registered_ws_channels_are_registered_here(self):
        """routing 中注册的每个 WS 通道都要在归属表登记（新增通道防漏）。"""

        registered = set(_registered_ws_regexes())
        assert registered == set(WS_CHANNEL_OWNERSHIP), (
            "WS 通道与归属登记表不一致：请在 ModuleSpec.ws_routes 声明归属并更新 "
            "WS_CHANNEL_OWNERSHIP（内核通道登记 None）"
        )

    def test_module_channels_declared_and_covered_by_ws_routes(self, module_config):
        """凡归属某模块的通道：声明必须覆盖样例路径，且停用该模块即被拦截。"""

        for regex, (module_id, sample) in WS_CHANNEL_OWNERSHIP.items():
            if module_id is None:
                continue
            spec = module_index()[module_id]
            assert any(re.match(route, sample) for route in spec.ws_routes), (
                f"模块 {module_id} 的 ws_routes 未覆盖通道 {regex}（样例 {sample}）"
            )
            module_config(disable=[module_id])
            assert is_ws_path_trimmed(sample), f"{sample} 应随模块 {module_id} 停用被拦截"

    def test_core_ws_channels_never_trimmed(self, module_config):
        module_config(preset="core")
        for regex, (module_id, sample) in WS_CHANNEL_OWNERSHIP.items():
            if module_id is None:
                assert not is_ws_path_trimmed(sample), f"内核通道 {regex} 不应被裁剪"

    def test_periodic_task_module_ids_are_declared(self):
        """周期任务 module= 归属必须是已声明模块 id（防拼写漂移导致裁剪静默失效）。"""

        declared = {spec.id for spec in all_module_specs()}
        unknown = sorted(
            {
                detail.get("module")
                for entry in get_register_period_tasks()
                for detail in entry.values()
                if detail.get("module") and detail["module"] not in declared
            }
        )
        assert unknown == [], f"以下周期任务归属了未声明的模块 id：{unknown}"
