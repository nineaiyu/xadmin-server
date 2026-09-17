# -*- coding: utf-8 -*-
"""功能模块裁剪矩阵演练（F1）：五层裁剪的运行期证据。

对应长期优化方案 §5.5 F1「裁剪矩阵演练」。五层与证据：

1. **请求路由**：停用模块 REST 路径经 ModuleGateMiddleware 直接 404（含业务码）；
2. **菜单/权限点**：`compute_hidden_menu_pks` 双口径（菜单子树 + 权限点前缀），
   本文件用 loadjson 种子行验证（运行期与种子导入同源，见 test_module_seed.py）；
3. **周期任务**：`create_or_update_registered_periodic_tasks` 跳过停用模块任务
   并清理历史注册条目；
4. **种子导入**：tests/unit/system/test_module_seed.py（过滤口径与运行期同源）；
5. **缓存失效**：`invalidate_trimmed_caches` 清理菜单路由 / 权限码缓存。

演练样本固定 `chat`：具备路由 / 菜单 / 权限点 / 周期任务 / 种子五类声明，
是覆盖面最完整的模块之一。裁剪语义红线：只隐藏与拦截、不删除业务数据。
"""

import json
import os

import pytest
from django.conf import settings as dj_settings
from django.test import Client

from common.celery.decorator import get_register_period_tasks
from common.core.modules import (
    all_module_specs,
    compute_hidden_menu_pks,
    disabled_permission_prefixes,
    invalidate_trimmed_caches,
    is_module_enabled,
    module_index,
    permission_prefixes_of,
)

TRIMMED = "chat"
LOADJSON_DIR = os.path.join(dj_settings.PROJECT_DIR, "loadjson")

pytestmark = pytest.mark.django_db


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


class TestDeclarationConsistency:
    def test_all_declared_modules_have_unique_ids(self):
        ids = [spec.id for spec in all_module_specs()]
        assert len(ids) == len(set(ids))

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
