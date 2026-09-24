# -*- coding: utf-8 -*-
"""API 查询能力测试：`?fields=` 字段子集 + 受控 lookup 透传（试点 2 页）。

口径钉死：

- `?fields=` **只收窄不提权**：与序列化器声明及字段权限求交，未知字段静默忽略；
- 受控 lookup 字段面 = filterset 已声明字段（另加恒可用的 pk），
  未声明 / 非法 lookup / 跨关系 / 非法值 / 条件超限一律 400（fail-closed，不落 500）；
- **过滤侧信道守护**：非超管对字段权限白名单外的字段过滤必须 400（不是空集）；
- 未开启 `controlled_lookup` 的视图收到 `__` 参数零变化（忽略）。
"""

from types import SimpleNamespace

import pytest
from django.conf import settings

from common.core.filter import ControlledLookupFilterBackend
from system.models import OperationLog, UserInfo

pytestmark = pytest.mark.django_db

USER_URL = "/api/system/user"
LOG_URL = "/api/system/logs/operation"
BACKEND = ControlledLookupFilterBackend


@pytest.fixture
def lookup_users(db):
    users = []
    for name, nickname in (("lookup_alpha", "Alpha"), ("lookup_beta", "Beta"), ("lookup_gamma", "")):
        users.append(UserInfo.objects.create_user(username=name, password="Test@123456", nickname=nickname))
    return users


def _rows(resp):
    assert resp.status_code == 200, resp.content
    return resp.json()["data"]["results"]


class TestFieldsSubset:
    def test_returns_only_requested_fields(self, auth_client, lookup_users):
        resp = auth_client.get(
            USER_URL,
            {"page": 1, "size": 100, "fields": "username,nickname", "username__startswith": "lookup_"},
        )
        rows = _rows(resp)
        assert len(rows) == 3
        assert all(set(row.keys()) == {"username", "nickname"} for row in rows)

    def test_unknown_fields_silently_ignored(self, auth_client, lookup_users):
        resp = auth_client.get(
            USER_URL,
            {"page": 1, "size": 100, "fields": "username,not_a_field", "username__startswith": "lookup_"},
        )
        rows = _rows(resp)
        assert len(rows) == 3
        assert all(set(row.keys()) == {"username"} for row in rows)

    def test_fields_not_widening_permission(self, auth_client, lookup_users):
        """请求未声明字段不报错、不影响响应（只收窄，不扩大）。"""
        resp = auth_client.get(USER_URL, {"page": 1, "size": 100, "fields": "pk,username"})
        rows = _rows(resp)
        assert {"pk", "username"} == set(rows[0].keys())


class TestControlledLookup:
    def test_icontains_and_exact(self, auth_client, lookup_users):
        rows = _rows(auth_client.get(USER_URL, {"page": 1, "size": 100, "username__icontains": "ALPHA"}))
        assert [row["username"] for row in rows] == ["lookup_alpha"]

        rows = _rows(auth_client.get(USER_URL, {"page": 1, "size": 100, "is_active__exact": "true"}))
        assert len(rows) >= 3

    def test_in_and_ne_and_isnull(self, auth_client, lookup_users):
        rows = _rows(auth_client.get(USER_URL, {"page": 1, "size": 100, "username__in": "lookup_alpha,lookup_beta"}))
        assert {row["username"] for row in rows} == {"lookup_alpha", "lookup_beta"}

        rows = _rows(
            auth_client.get(
                USER_URL,
                {"page": 1, "size": 100, "username__startswith": "lookup_", "username__ne": "lookup_alpha"},
            )
        )
        assert {row["username"] for row in rows} == {"lookup_beta", "lookup_gamma"}

        rows = _rows(auth_client.get(USER_URL, {"page": 1, "size": 100, "username__isnull": "false"}))
        assert len(rows) >= 3

    def test_in_accepts_repeated_params(self, auth_client, lookup_users):
        """重复参数（前端数组序列化 arrayFormat=repeat）与逗号分隔等价，不再只命中第一个值。"""
        rows = _rows(
            auth_client.get(USER_URL, {"page": 1, "size": 100, "username__in": ["lookup_alpha", "lookup_beta"]})
        )
        assert {row["username"] for row in rows} == {"lookup_alpha", "lookup_beta"}

    def test_search_columns_exposes_field_lookups(self, auth_client, lookup_users):
        """列元数据下发字段级 lookups（前端高级筛选候选与条件同源），主键按 key 兜底命中。"""
        resp = auth_client.get(f"{USER_URL}/search-columns")
        assert resp.status_code == 200, resp.content
        columns = {item["key"]: item for item in resp.json()["data"]}
        assert columns["gender"]["lookups"] == ["exact", "in", "gte", "lte", "isnull", "ne"]
        assert "exact" in columns["pk"]["lookups"]
        assert "lookups" not in columns.get("avatar", {})

    def test_pk_in(self, auth_client, lookup_users):
        pks = ",".join(str(user.pk) for user in lookup_users[:2])
        rows = _rows(auth_client.get(USER_URL, {"page": 1, "size": 100, "pk__in": pks}))
        assert {int(row["pk"]) for row in rows} == {user.pk for user in lookup_users[:2]}

    def test_gte_with_datetime_value(self, auth_client, lookup_users):
        rows = _rows(
            auth_client.get(
                USER_URL,
                {
                    "page": 1,
                    "size": 100,
                    "username__startswith": "lookup_",
                    "created_time__gte": "2000-01-01T00:00:00+08:00",
                },
            )
        )
        assert len(rows) == 3

    def test_operation_log_pilot(self, auth_client):
        OperationLog.objects.create(module="f13-probe", method="GET", path="/api/f13", status_code=1000)
        OperationLog.objects.create(module="f13-other", method="POST", path="/api/f13", status_code=1001)

        rows = _rows(auth_client.get(LOG_URL, {"page": 1, "size": 50, "module__icontains": "f13-probe"}))
        assert [row["module"] for row in rows] == ["f13-probe"]

        rows = _rows(auth_client.get(LOG_URL, {"page": 1, "size": 50, "path__exact": "/api/f13", "method__ne": "POST"}))
        assert rows and all(row["method"] == "GET" for row in rows)

    def test_undeclared_field_rejected(self, auth_client, lookup_users):
        resp = auth_client.get(USER_URL, {"page": 1, "size": 100, "not_a_field__icontains": "x"})
        assert resp.status_code == 400, resp.content

    def test_invalid_lookup_rejected(self, auth_client, lookup_users):
        for param in ("username__regex", "username__icontains__x", "username__"):
            resp = auth_client.get(USER_URL, {"page": 1, "size": 100, param: "x"})
            assert resp.status_code == 400, f"{param}: {resp.content}"

    def test_cross_relation_rejected(self, auth_client, lookup_users):
        resp = auth_client.get(USER_URL, {"page": 1, "size": 100, "dept__name__icontains": "x"})
        assert resp.status_code == 400, resp.content

    def test_invalid_value_rejected_not_500(self, auth_client, lookup_users):
        resp = auth_client.get(USER_URL, {"page": 1, "size": 100, "created_time__gte": "not-a-date"})
        assert resp.status_code == 400, resp.content

        resp = auth_client.get(USER_URL, {"page": 1, "size": 100, "username__in": " , "})
        assert resp.status_code == 400, resp.content

    def test_too_many_conditions_rejected(self, auth_client, lookup_users):
        params = {"page": 1, "size": 100}
        params.update({f"field{index}__exact": "x" for index in range(BACKEND.max_conditions + 1)})
        resp = auth_client.get(USER_URL, params)
        assert resp.status_code == 400, resp.content

    def test_view_without_opt_in_ignores_lookup(self, auth_client):
        """未开启 controlled_lookup 的视图（角色）：`__` 参数被忽略（零变化）。"""
        resp = auth_client.get("/api/system/role", {"page": 1, "size": 100, "name__icontains": "不存在的角色"})
        rows = _rows(resp)
        assert len(rows) >= 2  # 内置角色仍返回（参数未生效）


class TestFieldVisibilityFailClosed:
    """`_field_visible` 与序列化器字段裁剪同口径（超管全量 / 其余 fail-closed）。"""

    @staticmethod
    def _visible(request, field="nickname"):
        return BACKEND._field_visible(request, "system.userinfo", field)

    def test_superuser_allowed(self):
        user = SimpleNamespace(is_superuser=True)
        assert self._visible(SimpleNamespace(user=user)) is True

    def test_missing_fields_denied(self, monkeypatch):
        monkeypatch.setattr(settings, "PERMISSION_FIELD_ENABLED", True)
        user = SimpleNamespace(is_superuser=False)
        assert self._visible(SimpleNamespace(user=user)) is False
        assert self._visible(SimpleNamespace(user=user, fields=None)) is False

    def test_whitelist_hit_and_miss(self, monkeypatch):
        monkeypatch.setattr(settings, "PERMISSION_FIELD_ENABLED", True)
        user = SimpleNamespace(is_superuser=False)
        request = SimpleNamespace(user=user, fields={"system.userinfo": {"username"}})
        assert self._visible(request, "username") is True
        assert self._visible(request, "nickname") is False

    def test_field_permission_disabled_allows(self, monkeypatch):
        monkeypatch.setattr(settings, "PERMISSION_FIELD_ENABLED", False)
        user = SimpleNamespace(is_superuser=False)
        assert self._visible(SimpleNamespace(user=user)) is True

    def test_pk_always_allowed(self, monkeypatch):
        monkeypatch.setattr(settings, "PERMISSION_FIELD_ENABLED", True)
        user = SimpleNamespace(is_superuser=False)
        assert self._visible(SimpleNamespace(user=user), "pk") is True
