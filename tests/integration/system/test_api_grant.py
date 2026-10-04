# -*- coding: utf-8 -*-
"""应用级资源授权：模型 × 动作 × 字段 × 行四级收敛。

纪律：
- 凭证类断言必须用独立 APIClient（auth_client 是 force_authenticate，PAT 头不参与认证）；
- 应用 owner 为超管：四级收敛挂在凭证维度，超管身份不豁免（本文件显式断言）；
- 菜单与模型标签需显式构造（测试库不含种子菜单/标签），`_clean_cache` 每次清缓存。
"""

import pytest
from django.core.cache import cache as django_cache
from rest_framework.test import APIClient

from system.models.field import ModelLabelField
from system.models.token import ApiApplicationGrant

pytestmark = pytest.mark.django_db

APPS_URL = "/api/system/api-applications"
TOKEN_URL = "/api/system/open/token"
USER_URL = "/api/system/user"


def _create_application(client, **payload):
    data = {"name": "授权测试应用", "rate_limit_per_minute": 0}
    data.update(payload)
    resp = client.post(APPS_URL, data, format="json")
    assert resp.status_code == 201, resp.data
    return resp.data["data"]


def _issue_raw(application):
    resp = APIClient().post(
        TOKEN_URL,
        {"client_id": application["client_id"], "client_secret": application["client_secret"]},
        format="json",
    )
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]["access_token"]


def _pat_client(raw_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Pat {raw_token}")
    return client


def _put_grants(client, application, grants):
    resp = client.put(f"{APPS_URL}/{application['pk']}/grants", {"grants": grants}, format="json")
    assert resp.data["code"] == 1000, resp.data
    return resp.data["data"]["results"]


@pytest.fixture
def user_menus(db, menu_factory):
    """SystemUser 的 list/retrieve 权限菜单 + 模型标签（system.userinfo），另备 system.deptinfo。"""
    model_label = ModelLabelField.objects.create(name="system.userinfo", label="用户信息")
    ModelLabelField.objects.create(name="username", label="用户名", parent=model_label)
    menus = {}
    for action, method, path in (
        ("list", "GET", "api/system/user$"),
        ("retrieve", "GET", "api/system/user/(?P<pk>[^/.]+)$"),
    ):
        menu = menu_factory(f"{action}:SystemUser", path=path, method=method)
        menu.model.add(model_label)
        menus[action] = menu
    dept_label = ModelLabelField.objects.create(name="system.deptinfo", label="部门")
    dept_menu = menu_factory("list:SystemDept", path="api/system/dept$", method="GET")
    dept_menu.model.add(dept_label)
    return menus


class TestGrantEnforcement:
    """四级收敛的越权矩阵（应用凭证 = owner 超管身份，授权仍生效）。"""

    def test_compat_mode_without_grants_allows_covered_paths(self, auth_client, user_menus):
        """无规则 = 兼容模式：一期行为（owner 权限 + scopes）不受影响。"""
        application = _create_application(auth_client)
        client = _pat_client(_issue_raw(application))
        assert client.get(USER_URL).status_code == 200

    def test_grants_stored_per_application(self, auth_client, user_menus):
        """规则落库归属应用；删除应用级联清理。"""
        application = _create_application(auth_client)
        _put_grants(auth_client, application, [{"model": "system.userinfo", "actions": ["list"]}])
        assert ApiApplicationGrant.objects.filter(application_id=application["pk"]).count() == 1
        assert "client_secret" not in str(ApiApplicationGrant.objects.first())

    def test_whitelist_mode_denies_uncovered_model(self, auth_client, user_menus):
        application = _create_application(auth_client)
        _put_grants(auth_client, application, [{"model": "system.userinfo", "actions": ["list"]}])
        client = _pat_client(_issue_raw(application))
        assert client.get(USER_URL).status_code == 200
        assert client.get("/api/system/dept").status_code == 403

    def test_whitelist_mode_denies_uncovered_action(self, auth_client, superuser, user_menus):
        application = _create_application(auth_client)
        _put_grants(auth_client, application, [{"model": "system.userinfo", "actions": ["retrieve"]}])
        client = _pat_client(_issue_raw(application))
        assert client.get(USER_URL).status_code == 403  # list 未授权
        assert client.get(f"{USER_URL}/{superuser.pk}").status_code == 200

    def test_wildcard_model_action_allows(self, auth_client, user_menus):
        application = _create_application(auth_client)
        _put_grants(auth_client, application, [{"model": "*", "actions": ["list"]}])
        client = _pat_client(_issue_raw(application))
        assert client.get(USER_URL).status_code == 200
        assert client.get("/api/system/dept").status_code == 200
        # 动作级仍然收敛：retrieve 未授权
        assert client.get(f"{USER_URL}/1").status_code == 403

    def test_field_level_convergence_cuts_output(self, auth_client, user_menus):
        """字段级：输出白名单（与用户字段权限取交集），穿透字段权限豁免。"""
        application = _create_application(auth_client)
        _put_grants(
            auth_client,
            application,
            [{"model": "system.userinfo", "actions": ["list"], "fields": ["username"]}],
        )
        client = _pat_client(_issue_raw(application))
        resp = client.get(USER_URL)
        assert resp.status_code == 200
        rows = resp.data["data"]["results"]
        assert rows
        assert set(rows[0].keys()) == {"username"}

    def test_row_level_filter_scopes_rows(self, auth_client, superuser, normal_user, user_menus):
        """行级：编译为 Q 叠加在数据权限之后（超管 owner 同样生效）。"""
        application = _create_application(auth_client)
        _put_grants(
            auth_client,
            application,
            [
                {
                    "model": "system.userinfo",
                    "actions": ["list"],
                    "row_filter": [{"field": "username", "match": "icontains", "value": "admin", "type": "value.text"}],
                }
            ],
        )
        client = _pat_client(_issue_raw(application))
        resp = client.get(USER_URL)
        assert resp.status_code == 200
        usernames = [row["username"] for row in resp.data["data"]["results"]]
        assert usernames == ["admin"]  # zhangsan 被行级过滤掉

    def test_disabled_grant_falls_back_to_compat(self, auth_client, user_menus):
        """停用规则 = 不计入白名单：仅剩停用规则时回到兼容模式。"""
        application = _create_application(auth_client)
        _put_grants(
            auth_client,
            application,
            [{"model": "system.userinfo", "actions": ["list"], "is_active": False}],
        )
        client = _pat_client(_issue_raw(application))
        assert client.get(USER_URL).status_code == 200
        assert client.get("/api/system/dept").status_code == 200


class TestGrantManagement:
    def test_put_grants_replaces_all(self, auth_client, user_menus):
        application = _create_application(auth_client)
        first = _put_grants(
            auth_client,
            application,
            [
                {"model": "system.userinfo", "actions": ["list"]},
                {"model": "system.deptinfo", "actions": ["list"]},
            ],
        )
        assert {row["model"] for row in first} == {"system.userinfo", "system.deptinfo"}

        # 全量替换：保留第一条（带 pk 更新），删除第二条
        kept_pk = next(row["pk"] for row in first if row["model"] == "system.userinfo")
        rows = _put_grants(
            auth_client,
            application,
            [{"pk": kept_pk, "model": "system.userinfo", "actions": ["list", "retrieve"]}],
        )
        assert len(rows) == 1
        assert rows[0]["pk"] == kept_pk
        assert sorted(rows[0]["actions"]) == ["list", "retrieve"]
        assert ApiApplicationGrant.objects.filter(application_id=application["pk"]).count() == 1

    def test_validation_rejects_invalid_payloads(self, auth_client, user_menus):
        application = _create_application(auth_client)
        cases = [
            [{"model": "system.unknown", "actions": ["list"]}],
            [{"model": "system.userinfo", "actions": ["unknown-action"]}],
            [{"model": "system.userinfo", "actions": ["list"], "fields": ["no_such_field"]}],
            [{"model": "*", "actions": ["list"], "fields": ["username"]}],
            [{"model": "system.userinfo", "actions": []}],
            [
                {
                    "model": "system.userinfo",
                    "actions": ["list"],
                    "row_filter": [{"field": "no_such_field", "match": "exact", "value": "x", "type": "value.text"}],
                }
            ],
        ]
        for payload in cases:
            resp = auth_client.put(f"{APPS_URL}/{application['pk']}/grants", {"grants": payload}, format="json")
            assert resp.status_code == 400, payload

    def test_grants_requires_put_payload_list(self, auth_client, user_menus):
        application = _create_application(auth_client)
        resp = auth_client.put(f"{APPS_URL}/{application['pk']}/grants", {"grants": "oops"}, format="json")
        assert resp.data["code"] == 1001


class TestMenuMatchBoundary:
    """菜单解析的段边界口径（对抗性）：不得跨字符粘连、不得漏覆盖子路径。"""

    def test_no_cross_char_prefix_match(self):
        from system.utils.identity.api_grant import _match_menu_pk

        data = {"api/system/user": "pk-a"}
        assert _match_menu_pk(data, "/api/system/userfoo") is None
        assert _match_menu_pk(data, "/api/system/user-exports") is None

    def test_segment_prefix_covers_children(self):
        from system.utils.identity.api_grant import _match_menu_pk

        data = {"api/system/user": "pk-a"}
        assert _match_menu_pk(data, "/api/system/user") == "pk-a"
        assert _match_menu_pk(data, "/api/system/user/1") == "pk-a"

    def test_exact_anchor_keeps_exact_semantics(self):
        from system.utils.identity.api_grant import _match_menu_pk

        data = {"api/system/user$": "pk-a"}
        assert _match_menu_pk(data, "/api/system/user") == "pk-a"
        assert _match_menu_pk(data, "/api/system/user/1") is None

    def test_match_parity_with_runtime_chain(self, menu_factory):
        """开放平台菜单解析与运行期判定必须同源（历史上一处漏改锚定导致偏差）。"""
        from types import SimpleNamespace

        from common.core.permission import get_menu_pk
        from system.models import Menu
        from system.utils.identity.api_grant import resolve_request_menu_pk

        menu_factory("list:SystemUser", path="api/system/user", method="GET")
        menu_factory("list:SystemDept", path="api/system/dept$", method="GET")
        menus = list(Menu.objects.filter(menu_type=Menu.MenuChoices.PERMISSION, method="GET"))
        permission_data = {menu.path: (menu.pk, None) for menu in menus}
        for url in (
            "/api/system/user",
            "/api/system/user/1",
            "/api/system/userfoo",
            "/api/system/user-exports",
            "/api/system/dept",
            "/api/system/dept/1",
        ):
            runtime = get_menu_pk(permission_data, url)
            expected = runtime[0] if runtime else None
            request = SimpleNamespace(path_info=url, path=url, method="GET")
            assert resolve_request_menu_pk(request) == expected, url


class TestMenuPathCache:
    """path→pk 映射短缓存：按 HTTP 方法维度缓存、菜单变更信号即时失效、缓存故障降级直查。"""

    @staticmethod
    def _request(url, method="GET"):
        from types import SimpleNamespace

        return SimpleNamespace(path_info=url, path=url, method=method)

    def test_second_lookup_hits_cache(self, menu_factory):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from system.utils.identity.api_grant import invalid_menu_path_cache, resolve_request_menu_pk

        menu = menu_factory("list:SystemUser", path="api/system/user$", method="GET")
        invalid_menu_path_cache()  # 清掉工厂创建期间可能写入的缓存
        request = self._request("/api/system/user")
        with CaptureQueriesContext(connection) as first_ctx:
            first = resolve_request_menu_pk(request)
        assert first == menu.pk
        assert len(first_ctx.captured_queries) == 1  # 首次全表 values_list
        with CaptureQueriesContext(connection) as second_ctx:
            second = resolve_request_menu_pk(request)
        assert second == menu.pk
        assert len(second_ctx.captured_queries) == 0  # 第二次命中缓存

    def test_method_dimension_is_independent(self, menu_factory):
        from system.utils.identity.api_grant import invalid_menu_path_cache, resolve_request_menu_pk

        menu_factory("list:SystemUser", path="api/system/user$", method="GET")
        invalid_menu_path_cache()
        assert resolve_request_menu_pk(self._request("/api/system/user", "GET")) is not None
        # POST 维度的映射独立缓存，无 POST 权限菜单时不得被 GET 键串台
        assert resolve_request_menu_pk(self._request("/api/system/user", "POST")) is None

    def test_menu_change_signal_invalidates_cache(self, menu_factory):
        from system.utils.identity.api_grant import resolve_request_menu_pk

        request = self._request("/api/system/user")
        assert resolve_request_menu_pk(request) is None  # 空映射同样入缓存
        menu = menu_factory("list:SystemUser", path="api/system/user$", method="GET")
        # 菜单保存信号已失效映射缓存：无需等待 TTL 即可见
        assert resolve_request_menu_pk(request) == menu.pk

    def test_cache_failure_falls_back_to_query(self, menu_factory, monkeypatch):
        from system.utils.identity.api_grant import resolve_request_menu_pk

        menu = menu_factory("list:SystemUser", path="api/system/user$", method="GET")

        def boom(*args, **kwargs):
            raise RuntimeError("cache down")

        monkeypatch.setattr("system.utils.identity.api_grant.cache.get", boom)
        monkeypatch.setattr("system.utils.identity.api_grant.cache.set", boom)
        assert resolve_request_menu_pk(self._request("/api/system/user")) == menu.pk


class TestGrantOptions:
    def test_catalog_lists_models_actions_fields(self, auth_client, user_menus):
        resp = auth_client.get(f"{APPS_URL}/grant-options")
        assert resp.data["code"] == 1000
        models = resp.data["data"]["models"]
        assert models[0]["value"] == "*"
        target = next(item for item in models if item["value"] == "system.userinfo")
        assert {"list", "retrieve"} <= {action["value"] for action in target["actions"]}
        assert "username" in {field["value"] for field in target["fields"]}

    def test_catalog_requires_login(self, api_client):
        assert api_client.get(f"{APPS_URL}/grant-options").status_code == 401


class TestGrantValidationScope:
    """写入校验面与展示面同源：非超管管理员只能保存本人可授权的模型/动作/字段。"""

    @staticmethod
    def _serializer(user, payload):
        from rest_framework.test import APIRequestFactory

        from system.serializers.token import ApiApplicationGrantSerializer

        request = APIRequestFactory().post(APPS_URL)
        request.user = user
        return ApiApplicationGrantSerializer(data=payload, context={"request": request})

    def test_superuser_can_grant_any_known_model(self, superuser, user_menus):
        serializer = self._serializer(superuser, {"model": "system.deptinfo", "actions": ["list"]})
        assert serializer.is_valid(), serializer.errors

    def test_normal_user_scoped_to_own_grantable_models(self, normal_user, role, user_menus):
        """普通管理员：仅本人菜单覆盖的模型/动作/字段可保存，面外一律拒绝。"""
        role.menu.add(user_menus["list"])  # 仅 SystemUser 的 list 菜单
        django_cache.clear()  # 权限数据 24h 缓存：授权变更后需失效

        in_scope = self._serializer(
            normal_user, {"model": "system.userinfo", "actions": ["list"], "fields": ["username"]}
        )
        assert in_scope.is_valid(), in_scope.errors

        # 面外模型（部门菜单存在但未授予该用户）
        out_of_model = self._serializer(normal_user, {"model": "system.deptinfo", "actions": ["list"]})
        assert not out_of_model.is_valid()
        # 面外动作（用户只有 list，没有 create）
        out_of_action = self._serializer(normal_user, {"model": "system.userinfo", "actions": ["create"]})
        assert not out_of_action.is_valid()
        # 面外字段
        out_of_field = self._serializer(
            normal_user, {"model": "system.userinfo", "actions": ["list"], "fields": ["nickname"]}
        )
        assert not out_of_field.is_valid()
