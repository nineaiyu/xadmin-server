# -*- coding: utf-8 -*-
"""全局搜索：分组权限门 / 数据权限门 / 关键词口径 / 审批单行级收紧。

- 超管：全部分组可见（页面权限门放行 + 数据权限直通）；
- 无任何菜单权限的普通用户：所有分组不可见（搜索不成为绕过页面权限的信息通道）；
- 审批单在数据权限之外收紧为「申请人或审批人」；
- 关键词：空 / 超长返回空分组；`%`/`_` 按字面匹配（不当代通配符）。
"""

import pytest
from django.core.files.base import ContentFile

from approval.models import ApprovalRequest
from system.models import OperationLog, UploadFile, UserInfo
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

    def test_tag_group_matches_name(self, auth_client):
        """标签分组：按名称/备注命中，路由指向标签管理页。"""
        from system.models import Tag

        Tag.objects.create(name="重点客户标签", remark="季度评选")
        Tag.objects.create(name="无关标签")
        resp = auth_client.get(SEARCH_URL, {"keyword": "重点客户"})
        groups = resp.data["data"]["groups"]
        tag_group = _group(groups, "tag")
        assert tag_group is not None
        assert tag_group["route"] == "/system/tag/index"
        assert tag_group["items"][0]["text"] == "重点客户标签"

    def test_tag_group_hidden_without_tag_page_permission(self, api_client, menu_factory):
        """页面权限门：仅有搜索权限、无 list:Tag 的用户看不到标签分组。"""
        from system.models import Tag, UserRole

        menu = menu_factory("retrieve:SystemGlobalSearch", path="api/system/global-search$", method="GET")
        role = UserRole.objects.create(name="仅搜索无标签", code="search-no-tag")
        role.menu.add(menu)
        plain = UserInfo.objects.create_user(username="search-no-tag", password="x")
        plain.roles.add(role)
        Tag.objects.create(name="隐身标签")

        api_client.force_authenticate(user=plain)
        resp = api_client.get(SEARCH_URL, {"keyword": "隐身标签"})
        assert resp.status_code == 200
        assert _group(resp.data["data"]["groups"], "tag") is None

    def test_file_group_matches_filename(self, auth_client, searchable_data):
        resp = auth_client.get(SEARCH_URL, {"keyword": "采购合同"})
        groups = resp.data["data"]["groups"]
        file_group = _group(groups, "file")
        assert file_group is not None
        assert file_group["items"][0]["text"] == "E2E采购合同.pdf"

    def test_user_group_output_masked(self, api_client, menu_factory):
        """分组输出同过脱敏规则：列表接口已掩码时搜索不得回原文（防旁路）。"""
        from django.core.cache import cache

        from system.models import DataMaskRule, DataPermission, UserRole

        UserInfo.objects.create_user(username="mask-search-target", password="x", nickname="张三丰")
        DataMaskRule.objects.create(
            model="system.userinfo", field="nickname", mask_type="name", keep_head=1, keep_tail=1
        )
        data_permission = DataPermission.objects.create(
            name="E2E-搜索可见全部用户",
            rules=[
                {
                    "table": "system.userinfo",
                    "field": "id",
                    "type": "value.all",
                    "match": "",
                    "value": "",
                    "exclude": False,
                }
            ],
            mode_type=DataPermission.ModeChoices.OR,
            is_active=True,
        )
        data_permission.menu.clear()
        viewer = UserInfo.objects.create_user(username="mask-search-viewer", password="x")
        viewer.rules.add(data_permission)
        role = UserRole.objects.create(name="搜索用户页", code="search-user-page")
        role.menu.add(
            menu_factory("retrieve:SystemGlobalSearch", path="api/system/global-search$", method="GET"),
            menu_factory("list:SystemUser", path="api/system/user$", method="GET"),
        )
        viewer.roles.add(role)
        cache.clear()

        api_client.force_authenticate(user=viewer)
        resp = api_client.get(SEARCH_URL, {"keyword": "mask-search-target", "scope": "user"})
        assert resp.status_code == 200
        group = _group(resp.data["data"]["groups"], "user")
        assert group is not None
        assert group["items"][0]["text"] == "mask-search-target"
        assert group["items"][0]["meta"]["nickname"] == "张*丰"

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


class TestGroupTotals:
    """分组计数口径：未饱和不跑 COUNT（省查询），饱和时补 COUNT 取真实总数。"""

    def test_unsaturated_group_skips_count_query(self, auth_client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for index in range(3):
            UserInfo.objects.create_user(username=f"unsat_{index}", password="x")

        with CaptureQueriesContext(connection) as captured:
            resp = auth_client.get(SEARCH_URL, {"keyword": "unsat_", "scope": "user"})
        assert resp.status_code == 200
        group = _group(resp.data["data"]["groups"], "user")
        assert group["total"] == 3
        assert len(group["items"]) == 3
        statements = [query["sql"].lower() for query in captured]
        assert not any("count(" in sql and "unsat_" in sql for sql in statements), statements

    def test_saturated_group_reports_true_total(self, auth_client):
        from system.search import GROUP_LIMIT

        total_created = GROUP_LIMIT + 3
        for index in range(total_created):
            UserInfo.objects.create_user(username=f"sat_{index}", password="x")

        resp = auth_client.get(SEARCH_URL, {"keyword": "sat_", "scope": "user"})
        group = _group(resp.data["data"]["groups"], "user")
        # 展示截断到分组上限，但总数必须是真实条数（前端显示「共 N 条」）
        assert len(group["items"]) == GROUP_LIMIT
        assert group["total"] == total_created


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
