# -*- coding: utf-8 -*-
"""应用级资源授权（ADR-039 B1）：模型 × 动作 × 字段 × 行四级收敛。

纪律：
- 凭证类断言必须用独立 APIClient（auth_client 是 force_authenticate，PAT 头不参与认证）；
- 应用 owner 为超管：四级收敛挂在凭证维度，超管身份不豁免（本文件显式断言）；
- 菜单与模型标签需显式构造（测试库不含种子菜单/标签），`_clean_cache` 每次清缓存。
"""

import pytest
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
