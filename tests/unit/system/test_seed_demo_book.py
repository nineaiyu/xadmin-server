# -*- coding: utf-8 -*-
"""seed_demo_book：图书上架示例种子的幂等 / 清理 / 权限点清单守护。

三条纪律：
1. 幂等：重复执行不产生重复行（固定 pk update_or_create）；
2. 清理：--clean-only 移除流程 / 菜单 / 权限点与二次确认拦截路径，且不动其他配置项；
3. 清单对齐：``BookViewSet`` 的每个 action 都必须在 ``PERMISSION_PLAN`` 登记
   （demo 路由不参与 loadjson 权限点扫描，漏登记 = 非超管 403 且 CI 不报）。
"""

import pytest
from django.core.management import call_command

from common.core.config import SysConfig
from system.management.commands.seed_demo_book import (
    APPROVAL_PATTERNS,
    DIR_META_PK,
    FLOW_PK,
    MENU_META_PK,
    MENU_PK,
    NODE_PK,
    PERMISSION_PK_PREFIX,
    PERMISSION_PLAN,
)
from system.models import Menu, MenuMeta
from system.models.approval import ApprovalFlow, ApprovalFlowNode

pytestmark = pytest.mark.django_db

PERMISSION_PKS = [f"{PERMISSION_PK_PREFIX}{index:02d}" for index in range(1, len(PERMISSION_PLAN) + 1)]


def _to_action(method_name: str) -> str:
    """ViewSet 方法名 → 权限点动作名（batch_destroy → batchDestroy）。"""
    head, *tail = method_name.split("_")
    return head + "".join(part.title() for part in tail)


class TestSeedDemoBook:
    def test_generate_is_idempotent_and_cleanable(self):
        call_command("seed_demo_book")

        flow = ApprovalFlow.objects.get(pk=FLOW_PK)
        assert flow.code == "demo_book"
        assert ApprovalFlowNode.objects.filter(pk=NODE_PK, flow=flow).exists()
        assert Menu.objects.filter(pk=MENU_PK, menu_type=Menu.MenuChoices.MENU).exists()
        assert Menu.objects.filter(parent_id=MENU_PK, menu_type=Menu.MenuChoices.PERMISSION).count() == len(
            PERMISSION_PLAN
        )
        paths = SysConfig.APPROVAL_REQUIRED_PATHS or []
        for pattern in APPROVAL_PATTERNS:
            assert pattern in paths

        # 幂等：重复执行不产生重复行
        call_command("seed_demo_book")
        assert ApprovalFlow.objects.filter(pk=FLOW_PK).count() == 1
        assert ApprovalFlowNode.objects.filter(pk=NODE_PK).count() == 1
        assert Menu.all_objects.filter(pk=MENU_PK).count() == 1
        assert Menu.all_objects.filter(parent_id=MENU_PK, menu_type=Menu.MenuChoices.PERMISSION).count() == len(
            PERMISSION_PLAN
        )
        assert list(SysConfig.APPROVAL_REQUIRED_PATHS or []).count(APPROVAL_PATTERNS[0]) == 1

        # 清理：流程 / 菜单 / 权限点 / 拦截路径全部移除
        call_command("seed_demo_book", clean_only=True)
        assert not ApprovalFlow.objects.filter(pk=FLOW_PK).exists()
        assert not ApprovalFlowNode.objects.filter(pk=NODE_PK).exists()
        assert not Menu.all_objects.filter(pk__in=[MENU_PK, *PERMISSION_PKS]).exists()
        assert not MenuMeta.objects.filter(pk__in=[DIR_META_PK, MENU_META_PK]).exists()
        paths = SysConfig.APPROVAL_REQUIRED_PATHS or []
        for pattern in APPROVAL_PATTERNS:
            assert pattern not in paths

    def test_clean_only_keeps_other_paths(self):
        """清理只移出 demo 的拦截路径，不影响其他已配置项。"""
        SysConfig.set_value("APPROVAL_REQUIRED_PATHS", ["api/system/role/"])
        call_command("seed_demo_book")
        call_command("seed_demo_book", clean_only=True)
        assert SysConfig.APPROVAL_REQUIRED_PATHS == ["api/system/role/"]

    def test_demo_books_and_periodic_task(self, superuser):
        """示例书籍（开箱即演示）+ 周期任务种子：幂等生成、软删复活、清理移除。"""
        from django_celery_beat.models import PeriodicTask

        from demo.models import Book
        from system.management.commands.seed_demo_book import DEMO_BOOKS, PERIODIC_TASK_NAME, PERIODIC_TASK_PATH

        names = [item[0] for item in DEMO_BOOKS]
        call_command("seed_demo_book")
        assert Book.objects.filter(name__in=names).count() == len(DEMO_BOOKS)
        task = PeriodicTask.objects.get(name=PERIODIC_TASK_NAME)
        assert task.task == PERIODIC_TASK_PATH
        assert task.enabled is False  # 默认停用，任务管理页手动启用/立即运行

        # 幂等：重复执行不产生重复数据
        call_command("seed_demo_book")
        assert Book.all_objects.filter(name__in=names).count() == len(DEMO_BOOKS)
        assert PeriodicTask.objects.filter(name=PERIODIC_TASK_NAME).count() == 1

        # 曾在回收站的示例书籍重复执行后自动复活
        pk = Book.objects.get(name=names[0]).pk
        Book.objects.get(pk=pk).delete()
        assert not Book.objects.filter(pk=pk).exists()
        call_command("seed_demo_book")
        assert Book.objects.filter(pk=pk).exists()

        # 清理：示例数据与周期任务一并移除
        call_command("seed_demo_book", clean_only=True)
        assert not Book.all_objects.filter(name__in=names).exists()
        assert not PeriodicTask.objects.filter(name=PERIODIC_TASK_NAME).exists()

    def test_permission_plan_covers_viewset_actions(self):
        """BookViewSet 的自定义 action（含继承）必须登记在 PERMISSION_PLAN。

        口径：``@action`` 装饰的方法（``.mapping``）除元数据端点（search-columns /
        search-fields 与标准 CRUD 同口径，无需独立权限点）外都在计划内；
        计划内的动作也必须真实存在（标准 CRUD 或自定义 action）。
        """
        from demo.views import BookViewSet

        metadata_actions = {"search_columns", "search_fields", "metadata"}
        # 页面级权限：非 ViewSet action（如行级变更历史查操作日志端点），同样需要登记
        page_level = {"changeHistory"}
        mapping_actions = {
            name
            for klass in BookViewSet.__mro__
            for name, attr in vars(klass).items()
            if callable(attr) and hasattr(attr, "mapping")
        }
        planned = {item[0] for item in PERMISSION_PLAN}

        # 1) 自定义 action（非元数据）必须登记
        assert {_to_action(name) for name in mapping_actions - metadata_actions} <= planned
        # 2) 标准 CRUD 必须存在，且计划内的动作均由「标准 CRUD ∪ 自定义 action ∪ 页面级权限」覆盖
        standard = {"list", "create", "retrieve", "update", "partialUpdate", "destroy"}
        for method in ("list", "create", "retrieve", "update", "partial_update", "destroy"):
            assert hasattr(BookViewSet, method), f"BookViewSet 缺少标准 CRUD 方法 {method}"
        assert planned <= ({_to_action(name) for name in mapping_actions} | standard | page_level)
