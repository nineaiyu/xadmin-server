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
from audit.models import OperationLog
from file.models import UploadFile
from identity.models import UserInfo
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
        module="user", method="DELETE", path="/api/identity/user/1/", status="PENDING", creator=user
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

    def test_group_carries_search_field(self, auth_client, searchable_data):
        """分组下发 search_field（命中跳转承接字段）；未配置的实体为 None。"""
        resp = auth_client.get(SEARCH_URL, {"keyword": "alice"})
        groups = resp.data["data"]["groups"]
        assert _group(groups, "user")["search_field"] == "username"
        # 未配置承接字段的实体（审批单）单独用 path 关键词命中后断言为 None
        resp2 = auth_client.get(SEARCH_URL, {"keyword": "user/1"})
        approval_group = _group(resp2.data["data"]["groups"], "approval")
        assert approval_group is not None
        assert approval_group["search_field"] is None

    def test_search_field_aligned_with_list_page_filter(self):
        """承接字段必须是列表页真实搜索字段（否则跳转 query 不被 RePlusPage 承接）。"""
        from system.search import SEARCH_PROVIDERS

        by_key = {provider.key: provider for provider in SEARCH_PROVIDERS}
        # 列表页过滤器字段（与各 ViewSet/filter_class 对齐的口径锚点）
        expected = {
            "user": "username",
            "dept": "name",
            "post": "name",
            "file": "filename",
            "tag": "name",
            "approval": None,
            "leave": None,
            "log": None,
        }
        assert set(by_key) >= set(expected)
        for key, field in expected.items():
            assert by_key[key].search_field == field, key

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
        from identity.models import UserRole
        from system.models import Tag

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

        from audit.models import DataMaskRule
        from identity.models import UserRole
        from system.models import DataPermission

        UserInfo.objects.create_user(username="mask-search-target", password="x", nickname="张三丰")
        DataMaskRule.objects.create(
            model="identity.userinfo", field="nickname", mask_type="name", keep_head=1, keep_tail=1
        )
        data_permission = DataPermission.objects.create(
            name="E2E-搜索可见全部用户",
            rules=[
                {
                    "table": "identity.userinfo",
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
        list_menu = menu_factory("list:SystemUser", path="api/identity/user$", method="GET")
        role.menu.add(
            menu_factory("retrieve:SystemGlobalSearch", path="api/system/global-search$", method="GET"),
            list_menu,
        )
        viewer.roles.add(role)
        # 字段权限 fail-closed：无白名单时整组将被隐藏（见 TestGlobalSearchFieldPermission），
        # 本用例验证的是「字段可见但输出需要脱敏」，故显式授予 username/nickname
        from system.models import FieldPermission, ModelLabelField

        field_root = ModelLabelField.objects.create(
            name="identity.userinfo", label="用户", field_type=ModelLabelField.FieldChoices.ROLE
        )
        whitelist = [
            ModelLabelField.objects.create(
                name=name, label=name, parent=field_root, field_type=ModelLabelField.FieldChoices.ROLE
            )
            for name in ("username", "nickname")
        ]
        FieldPermission.objects.create(role=role, menu=list_menu).field.add(*whitelist)
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
        from identity.models import UserRole

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
            list_url="api/identity/user",
            queryset=lambda: UserInfo.objects.all(),
            text_fields=("username",),
            display_field="username",
        )
        defaults.update(kwargs)
        return SearchProvider(**defaults)

    def test_granted_page_permission_is_visible(self, db):
        provider = self._provider()
        assert provider.visible_to(self._plain_user(), {"api/identity/user$": ("menu-pk", None)})

    def test_missing_page_permission_is_hidden(self, db):
        assert not self._provider().visible_to(self._plain_user(), {})

    def test_superuser_only_group(self, db):
        provider = self._provider(superuser_only=True, list_url="api/audit/logs/operation")

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


class TestRegisterSearchProvider:
    """二开扩展点：运行期注册业务模型分组，key 冲突 fail-fast。"""

    @pytest.fixture(autouse=True)
    def _restore_providers(self):
        from system.search import SEARCH_PROVIDERS

        builtin = list(SEARCH_PROVIDERS)
        yield
        SEARCH_PROVIDERS[:] = builtin

    def test_registered_provider_searchable(self, auth_client):
        from system.search import SEARCH_PROVIDERS, SearchProvider, register_search_provider

        register_search_provider(
            SearchProvider(
                key="e2e-book",
                label="图书",
                route="/demo/book/index",
                list_url="api/demo/book",
                queryset=lambda: [],
                text_fields=("name",),
                display_field="name",
            )
        )
        assert any(provider.key == "e2e-book" for provider in SEARCH_PROVIDERS)

    def test_duplicate_key_rejected(self, auth_client):
        from system.search import SearchProvider, register_search_provider

        with pytest.raises(ValueError, match="duplicate"):
            register_search_provider(
                SearchProvider(
                    key="user",
                    label="重复",
                    route="/x",
                    list_url="api/x",
                    queryset=lambda: [],
                    text_fields=("name",),
                    display_field="name",
                )
            )


class TestGlobalSearchFieldPermission:
    """字段权限必须约束搜索：隐藏字段既不出现在结果里，也不能作为检索入口。"""

    def _setup(self, user, role, menu_factory):
        from system.models import FieldPermission, ModelLabelField

        search_menu = menu_factory("retrieve:SystemGlobalSearch", path="api/system/global-search$", method="GET")
        list_menu = menu_factory("list:SystemUser", path="api/identity/user$", method="GET")
        role.menu.add(search_menu, list_menu)
        if not user.roles.filter(pk=role.pk).exists():
            user.roles.add(role)
        parent = ModelLabelField.objects.create(
            name="identity.userinfo", label="用户", field_type=ModelLabelField.FieldChoices.ROLE
        )
        username = ModelLabelField.objects.create(
            name="username", label="用户名", parent=parent, field_type=ModelLabelField.FieldChoices.ROLE
        )
        FieldPermission.objects.create(role=role, menu=list_menu).field.add(username)
        from system.models import DataPermission

        user.rules.add(
            DataPermission.objects.create(
                name="字段权限搜索-全量用户",
                rules=[
                    {
                        "table": "identity.userinfo",
                        "field": "id",
                        "type": "value.all",
                        "match": "",
                        "value": "",
                        "exclude": False,
                    }
                ],
                is_active=True,
            )
        )
        UserInfo.objects.create_user(username="hidden-nickname-user", password="x", nickname="隐秘昵称")

    def test_hidden_fields_not_searchable_or_returned(self, api_client, normal_user, role, menu_factory):
        self._setup(normal_user, role, menu_factory)
        api_client.force_authenticate(user=normal_user)

        hidden = api_client.get(SEARCH_URL, {"keyword": "隐秘昵称"}).data["data"]["groups"]
        assert _group(hidden, "user") is None

        hit = api_client.get(SEARCH_URL, {"keyword": "hidden-nickname-user"}).data["data"]["groups"]
        user_group = _group(hit, "user")
        assert user_group is not None
        assert user_group["items"][0]["text"] == "hidden-nickname-user"
        assert "nickname" not in user_group["items"][0]["meta"]


class TestGlobalSearchConsistency:
    def test_tag_group_matches_page_scope_without_data_permission(self, api_client, normal_user, role, menu_factory):
        """标签页显式剥离数据权限：搜索分组必须同口径（否则页面可见、搜索为空）。"""
        from system.models import Tag

        search_menu = menu_factory("retrieve:SystemGlobalSearch", path="api/system/global-search$", method="GET")
        tag_menu = menu_factory("list:Tag", path="api/system/tags$", method="GET")
        role.menu.add(search_menu, tag_menu)
        normal_user.roles.add(role)
        # 字段权限 fail-closed：标签列表的显示字段需在白名单内
        from system.models import FieldPermission, ModelLabelField

        root = ModelLabelField.objects.create(
            name="system.tag", label="标签", field_type=ModelLabelField.FieldChoices.ROLE
        )
        name_field = ModelLabelField.objects.create(
            name="name", label="名称", parent=root, field_type=ModelLabelField.FieldChoices.ROLE
        )
        FieldPermission.objects.create(role=role, menu=tag_menu).field.add(name_field)
        Tag.objects.create(name="公共可见标签")
        api_client.force_authenticate(user=normal_user)

        groups = api_client.get(SEARCH_URL, {"keyword": "公共可见标签"}).data["data"]["groups"]

        assert _group(groups, "tag") is not None

    def test_group_labels_are_translated(self, auth_client, searchable_data):
        """分组标题走 gettext：英文界面不得显示中文分组名。"""
        resp = auth_client.get(SEARCH_URL, {"keyword": "alice"}, HTTP_ACCEPT_LANGUAGE="en-us")
        groups = resp.data["data"]["groups"]
        assert str(_group(groups, "user")["label"]) == "User"
