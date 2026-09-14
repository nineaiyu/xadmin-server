# -*- coding: utf-8 -*-
"""全局搜索：分组权限门 / 数据权限门 / 关键词口径 / 审批单行级收紧。

- 超管：全部分组可见（页面权限门放行 + 数据权限直通）；
- 无任何菜单权限的普通用户：所有分组不可见（搜索不成为绕过页面权限的信息通道）；
- 审批单在数据权限之外收紧为「申请人或审批人」；
- 关键词：空 / 超长返回空分组；`%`/`_` 按字面匹配（不当代通配符）。
"""

import pytest
from django.core.files.base import ContentFile

from system.models import ApprovalRequest, OperationLog, UploadFile, UserInfo
from system.search import _approval_row_scope

pytestmark = pytest.mark.django_db

SEARCH_URL = "/api/system/global-search"


@pytest.fixture
def searchable_data(db):
    user = UserInfo.objects.create_user(username="alice-search", password="x", nickname="爱丽丝")
    # 直接落一条含真实磁盘文件的 UploadFile（文件缺失会在 save 钩子计算 md5 时报错）
    row = UploadFile(filename="E2E采购合同.pdf", is_upload=True, is_tmp=False)
    row.filepath.save("e2e/E2E采购合同.pdf", ContentFile(b"e2e-contract"), save=True)
    OperationLog.objects.create(module="demo", method="GET", path="/api/demo/book/")
    approval = ApprovalRequest.objects.create(
        module="user", method="DELETE", path="/api/system/user/1/", status="PENDING", creator=user
    )
    return {"user": user, "approval": approval}


def _group(groups, key):
    return next((group for group in groups if group["key"] == key), None)


class TestGlobalSearchAPI:
    def test_superuser_gets_hit_groups(self, auth_client, searchable_data):
        resp = auth_client.get(SEARCH_URL, {"keyword": "alice"})
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        groups = resp.data["data"]["groups"]
        user_group = _group(groups, "user")
        assert user_group is not None
        assert user_group["route"] == "/system/user/index"
        assert user_group["items"][0]["text"] == "alice-search"
        assert user_group["items"][0]["meta"]["nickname"] == "爱丽丝"

    def test_file_group_matches_filename(self, auth_client, searchable_data):
        resp = auth_client.get(SEARCH_URL, {"keyword": "采购合同"})
        groups = resp.data["data"]["groups"]
        file_group = _group(groups, "file")
        assert file_group is not None
        assert file_group["items"][0]["text"] == "E2E采购合同.pdf"

    def test_scope_limits_to_single_group(self, auth_client, searchable_data):
        resp = auth_client.get(SEARCH_URL, {"keyword": "alice", "scope": "user"})
        groups = resp.data["data"]["groups"]
        assert [group["key"] for group in groups] == ["user"]

    def test_empty_or_overlong_keyword_returns_no_groups(self, auth_client, searchable_data):
        for keyword in ("", "   ", "a" * 51):
            resp = auth_client.get(SEARCH_URL, {"keyword": keyword})
            assert resp.data["data"]["groups"] == []

    def test_wildcard_keyword_is_safe(self, auth_client, searchable_data):
        """`%`/`_` 按 LIKE 通配符语义执行（参数化查询，无注入；权限门在查询集层）。"""
        resp = auth_client.get(SEARCH_URL, {"keyword": "%(_"})
        assert resp.status_code == 200
        assert isinstance(resp.data["data"]["groups"], list)

    def test_plain_user_without_search_permission_is_rejected(self, api_client, searchable_data):
        """URL 权限门：无 retrieve:SystemGlobalSearch 权限码的普通用户直接 403。"""
        plain = UserInfo.objects.create_user(username="plain-search", password="x")
        api_client.force_authenticate(user=plain)
        resp = api_client.get(SEARCH_URL, {"keyword": "alice"})
        assert resp.status_code == 403

    def test_plain_user_with_permission_but_no_data_grant_is_fail_closed(self, api_client, menu_factory):
        """两道门串联：页面权限门已过（授予搜索权限码），数据权限门 fail-closed（无授权 = 空分组）。"""
        from system.models import UserRole

        menu = menu_factory("retrieve:SystemGlobalSearch", path="api/system/global-search$", method="GET")
        role = UserRole.objects.create(name="仅搜索", code="search-only")
        role.menu.add(menu)
        plain = UserInfo.objects.create_user(username="granted-search", password="x")
        plain.roles.add(role)
        UserInfo.objects.create_user(username="visible-user", password="x")

        api_client.force_authenticate(user=plain)
        resp = api_client.get(SEARCH_URL, {"keyword": "visible"})
        assert resp.status_code == 200
        assert resp.data["data"]["groups"] == []


class TestPagePermissionGate:
    """分组级页面权限门（system/search.py::SearchProvider.visible_to）。"""

    @staticmethod
    def _plain_user():
        class PlainUser:
            is_superuser = False

        return PlainUser()

    def _provider(self, **kwargs):
        from system.search import SearchProvider

        defaults = dict(
            key="user",
            label="用户",
            route="/system/user/index",
            list_url="api/system/user",
            queryset=lambda: UserInfo.objects.all(),
            text_fields=("username",),
            display_field="username",
        )
        defaults.update(kwargs)
        return SearchProvider(**defaults)

    def test_granted_page_permission_is_visible(self, db):
        provider = self._provider()
        assert provider.visible_to(self._plain_user(), {"api/system/user$": ("menu-pk", None)})

    def test_missing_page_permission_is_hidden(self, db):
        assert not self._provider().visible_to(self._plain_user(), {})

    def test_superuser_only_group(self, db):
        provider = self._provider(superuser_only=True, list_url="api/system/logs/operation")

        class SuperUser:
            is_superuser = True

        class PlainUser:
            is_superuser = False

        assert provider.visible_to(SuperUser(), {})
        assert not provider.visible_to(PlainUser(), {})


class TestApprovalRowScope:
    def test_non_superuser_only_sees_own_requests(self, searchable_data):
        owner = searchable_data["user"]
        stranger = UserInfo.objects.create_user(username="stranger-search", password="x")
        queryset = ApprovalRequest.objects.all()
        assert _approval_row_scope(stranger, queryset).count() == 0
        assert _approval_row_scope(owner, queryset).count() == 1
        # 审批人同样可见
        searchable_data["approval"].approver = stranger
        searchable_data["approval"].save(update_fields=["approver"])
        assert _approval_row_scope(stranger, queryset).count() == 1

    def test_superuser_sees_all(self, superuser, searchable_data):
        assert _approval_row_scope(superuser, ApprovalRequest.objects.all()).count() == 1
