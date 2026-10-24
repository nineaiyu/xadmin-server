# -*- coding: utf-8 -*-
"""权限面三分类与未分类豁免收口：custom 权限面不再静默不可见。"""

import pytest

from system.services.permission_sync import scan
from system.services.permission_sync.declarations import PERMISSION_EXEMPT_VIEWS
from system.services.permission_sync.types import RouteInfo


class _Base:
    permission_classes = None


class _AuthView:
    from rest_framework.permissions import IsAuthenticated

    permission_classes = [IsAuthenticated]


class _PublicView:
    from rest_framework.permissions import AllowAny

    permission_classes = [AllowAny]


class _EmptyView:
    permission_classes = []


class _CustomView:
    permission_classes = [object]  # 任意自管权限类


def route(view: str, face: str) -> RouteInfo:
    return RouteInfo(
        view=view,
        name="",
        url="api/x/",
        sample="/api/x/",
        actions={},
        view_cls=_CustomView,
        requires_permission=face == "menu",
        permission_face=face,
    )


class TestPermissionFace:
    def test_menu_chain_and_default(self):
        assert scan.permission_face(_AuthView) == "menu"
        assert scan.permission_face(_Base) == "menu"  # 未声明 = 走默认链
        assert scan.requires_permission(_AuthView) is True

    def test_public_faces(self):
        assert scan.permission_face(_PublicView) == "public"
        assert scan.permission_face(_EmptyView) == "public"
        assert scan.requires_permission(_PublicView) is False

    def test_custom_face_is_not_menu(self):
        assert scan.permission_face(_CustomView) == "custom"
        assert scan.requires_permission(_CustomView) is False


class TestUnclassifiedViews:
    def test_exempted_custom_view_not_reported(self):
        assert "system.views.user.configs.ConfigsViewSet" in PERMISSION_EXEMPT_VIEWS
        routes = [route("system.views.user.configs.ConfigsViewSet", "custom")]
        assert scan.unclassified_views(routes) == []

    def test_new_custom_view_reported_sorted_deduped(self):
        routes = [
            route("b.views.Custom", "custom"),
            route("b.views.Custom", "custom"),  # 同视图多路由去重
            route("a.views.Custom", "custom"),
            route("a.views.Public", "public"),
            route("a.views.Menu", "menu"),
        ]
        assert scan.unclassified_views(routes) == ["a.views.Custom", "b.views.Custom"]


@pytest.mark.django_db
class TestSyncFailsLoudOnUnclassified:
    def test_sync_command_fails_when_custom_view_unexempted(self, monkeypatch):
        """未登记豁免的 custom 视图让 sync 失败（不再静默跳过 = 非超管 403 无告警）。"""
        from io import StringIO

        from django.core.management import call_command
        from django.core.management.base import CommandError

        # 命令经包名引用（from .scan import 的独立绑定），补丁打在包属性上
        monkeypatch.setattr(
            "system.services.permission_sync.build_route_index",
            lambda: [route("myapp.views.SelfManaged", "custom")],
        )
        out = StringIO()
        with pytest.raises(CommandError, match="PERMISSION_EXEMPT_VIEWS"):
            call_command("sync_menu_permissions", "--dry-run", stdout=out)
        assert "myapp.views.SelfManaged" in out.getvalue()

    def test_real_routes_have_no_unclassified(self):
        """当前全量路由零未分类（豁免清单完整）——新增 custom 视图的收口锚点。"""
        assert scan.unclassified_views(scan.build_route_index()) == []
