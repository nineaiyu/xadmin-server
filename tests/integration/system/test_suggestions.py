# -*- coding: utf-8 -*-
"""远程联想接口集成测试（首个消费方：审批委托的代理人字段）。

口径钉死：
- 候选集 = serializer 关联字段自身 queryset（数据权限内），与写入校验同源；
- 权限回落到资源 list 权限点（`suggestions$` URL 特例）；
- 字段白名单：ViewSet 的 `suggestion_fields` 声明外的一律拒绝 / 不下发
  （审批委托仅代理人走联想，委托人保持 api-search-user 弹窗选择器）；
- `search` / `pks` 至少给一个，否则返回空（禁止当全量导出接口用）；
- `limit` 硬上限 50；非关联字段拒绝（code=1001）。
"""

import pytest

from system.models import UserInfo

SUGGEST_URL = "/api/system/approval-delegations/suggestions"
COLUMNS_URL = "/api/system/approval-delegations/search-columns"

pytestmark = pytest.mark.django_db


@pytest.fixture
def delegator_user(db):
    return UserInfo.objects.create_user(username="zhangsan", password="x", nickname="张三")


@pytest.fixture
def delegate_user(db):
    return UserInfo.objects.create_user(username="lizhiguo", password="x", nickname="李团长")


@pytest.fixture
def delegation_menu(menu_factory):
    return menu_factory("list:SystemApprovalDelegation", path="api/system/approval-delegations$", method="GET")


class TestSuggestionsContract:
    def test_search_matches_label_format(self, auth_client, delegate_user):
        resp = auth_client.get(SUGGEST_URL, {"field": "delegate", "search": "李"})
        assert resp.status_code == 200
        assert resp.data["code"] == 1000, resp.data
        row = next(r for r in resp.data["data"] if str(r["pk"]) == str(delegate_user.pk))
        assert row["label"] == "李团长(lizhiguo)"
        assert str(row["value"]) == str(delegate_user.pk)

    def test_search_by_username(self, auth_client, delegate_user):
        resp = auth_client.get(SUGGEST_URL, {"field": "delegate", "search": "lizhi"})
        assert [str(r["pk"]) for r in resp.data["data"]] == [str(delegate_user.pk)]

    def test_missing_field_rejected(self, auth_client):
        resp = auth_client.get(SUGGEST_URL, {"search": "x"})
        assert resp.data["code"] == 1001

    def test_non_related_field_rejected(self, auth_client):
        resp = auth_client.get(SUGGEST_URL, {"field": "remark", "search": "x"})
        assert resp.data["code"] == 1001

    def test_field_outside_whitelist_rejected(self, auth_client, delegator_user):
        """委托人未入 suggestion_fields 白名单：即便同为用户关联字段也拒绝。"""
        resp = auth_client.get(SUGGEST_URL, {"field": "delegator", "search": "张"})
        assert resp.data["code"] == 1001

    def test_no_search_no_pks_returns_empty(self, auth_client, delegate_user):
        resp = auth_client.get(SUGGEST_URL, {"field": "delegate"})
        assert resp.data["code"] == 1000
        assert resp.data["data"] == []

    def test_pks_echo_without_keyword(self, auth_client, delegator_user, delegate_user):
        resp = auth_client.get(
            SUGGEST_URL,
            {"field": "delegate", "pks": f"{delegator_user.pk},{delegate_user.pk}"},
        )
        rows = {str(r["pk"]): r["label"] for r in resp.data["data"]}
        assert rows == {
            str(delegator_user.pk): "张三(zhangsan)",
            str(delegate_user.pk): "李团长(lizhiguo)",
        }

    def test_pks_ignores_unknown_pk(self, auth_client, delegate_user):
        resp = auth_client.get(SUGGEST_URL, {"field": "delegate", "pks": f"{delegate_user.pk},999999999"})
        assert [str(r["pk"]) for r in resp.data["data"]] == [str(delegate_user.pk)]

    def test_limit_hard_cap(self, auth_client, delegate_user):
        UserInfo.objects.bulk_create([UserInfo(username=f"u{i:02d}", password="x") for i in range(55)])
        resp = auth_client.get(SUGGEST_URL, {"field": "delegate", "search": "u", "limit": "999"})
        assert len(resp.data["data"]) == 50

    def test_search_columns_suggest_url_only_for_whitelisted_field(self, auth_client):
        """suggest_url 仅下发给白名单内的代理人；委托人（api-search-user 弹窗）不下发。"""
        resp = auth_client.get(COLUMNS_URL)
        assert resp.data["code"] == 1000
        columns = {item["key"]: item for item in resp.data["data"]}
        assert columns["delegate"]["suggest_url"] == "/api/system/approval-delegations/suggestions"
        assert "suggest_url" not in columns["delegator"]

    def test_inline_metadata_with_meta_exposes_suggest_url(self, auth_client):
        """页面首开元数据来自 with_meta=1 的 list 内联响应，必须同样下发 suggest_url。

        回归守护：内联路径下 request.path_info 是资源前缀本身（无 /search-columns
        后缀），get_suggest_url 的路径推导曾因此返回 None 导致联想不生效。
        """
        resp = auth_client.get("/api/system/approval-delegations", {"with_meta": "1"})
        assert resp.data["code"] == 1000
        columns = resp.data["data"].get("search_columns") or []
        delegate = next(item for item in columns if item["key"] == "delegate")
        assert delegate["suggest_url"] == "/api/system/approval-delegations/suggestions"


class TestSuggestionsPermission:
    def test_falls_back_to_list_permission(self, api_client, normal_user, role, delegation_menu, db):
        role.menu.add(delegation_menu)
        api_client.force_authenticate(user=normal_user)
        # suggestions 子路径无独立权限点，回落 list:SystemApprovalDelegation（URL 特例生效才会 200）
        resp = api_client.get(SUGGEST_URL, {"field": "delegate", "search": "x"})
        assert resp.status_code == 200, resp.data

    def test_denied_without_list_permission(self, api_client, normal_user):
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(SUGGEST_URL, {"field": "delegate", "search": "x"})
        assert resp.status_code == 403

    def test_data_permission_scopes_candidates(self, api_client, normal_user, role, delegation_menu, delegate_user):
        """有功能权限、无数据授权：fail-closed，候选为空（与 list 同一口径）。"""
        role.menu.add(delegation_menu)
        api_client.force_authenticate(user=normal_user)
        resp = api_client.get(SUGGEST_URL, {"field": "delegate", "search": "李"})
        assert resp.status_code == 200
        assert resp.data["data"] == []
