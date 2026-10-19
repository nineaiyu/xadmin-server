# -*- coding: utf-8 -*-
"""system.utils.platform.menu 中的 get_view_permissions 单元测试（URL registry 依赖真实路由）。"""

import pytest

import server.urls  # noqa: F401  # 预加载 urlconf，让 get_all_url_dict 的 import_string 生效
from system.utils.platform.menu import MENU_TITLE_MAX, _truncate_title, get_view_permissions

pytestmark = pytest.mark.django_db

USER_VIEW = "identity.views.admin.user.UserViewSet"
DELEGATION_VIEW = "approval.views.approval_delegation.ApprovalDelegationViewSet"
FILE_VIEW = "file.views.admin.file.UploadFileViewSet"


class TestGetViewPermissions:
    def test_user_viewset_returns_permissions(self):
        perms = get_view_permissions(USER_VIEW)
        assert perms
        for p in perms:
            assert p["method"]
            assert p["url"]
            assert p["code"]
            assert p["description"]

    def test_standard_actions_carry_models(self):
        perms = get_view_permissions(USER_VIEW)
        models = set()
        for p in perms:
            if p["models"]:
                models.update(p["models"])
        assert models
        assert "identity.userinfo" in models

    def test_unknown_view_returns_empty(self):
        assert get_view_permissions("identity.views.admin.user.NoSuchViewSet") == []


class TestMenuTitlePolicy:
    """菜单标题口径：首行短、无句号、{cls} 展开后仍短。"""

    def test_truncate_prefers_punctuation_and_strips_period(self):
        assert _truncate_title("批量删除：内置字典排除并点名，不因单条受保护而整批失败。") == "批量删除"
        assert _truncate_title("短标题。") == "短标题"
        assert _truncate_title("无标点的超长标题" * 4) == f"{'无标点的超长标题' * 4}"[: MENU_TITLE_MAX - 1] + "…"

    def test_generated_titles_fit_menu_width(self):
        for view in (USER_VIEW, DELEGATION_VIEW, FILE_VIEW):
            for perm in get_view_permissions(view):
                title = perm["description"]
                assert len(title) <= MENU_TITLE_MAX, f"{view} {perm['method']} {perm['url']}: {title}"
                assert not title.endswith("。"), title
                assert not title.endswith("…"), title

    def test_cls_template_uses_short_view_name(self):
        titles = {perm["description"] for perm in get_view_permissions(DELEGATION_VIEW) if perm["method"] == "GET"}
        assert "获取审批委托的列表" in titles
        assert "获取审批委托的详情" in titles

    def test_actions_have_distinct_titles(self):
        titles = {perm["description"] for perm in get_view_permissions(FILE_VIEW) if perm["method"] == "POST"}
        assert {"初始化分片上传", "上传分片", "完成分片上传", "中止分片上传"} <= titles
