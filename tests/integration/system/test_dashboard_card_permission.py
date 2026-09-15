# -*- coding: utf-8 -*-
"""仪表盘卡片级权限（layout[].allowed_roles）集成测试。"""

import pytest
from django.utils import timezone

from system.models import UserInfo, UserRole
from system.models.dataset import Dataset
from system.utils.dataset import filter_layout_for_user

pytestmark = pytest.mark.django_db

DASHBOARDS_URL = "/api/system/dashboards"


@pytest.fixture
def dataset(db):
    return Dataset.objects.create(name="card_ds", bound_model="system.userinfo", visibility="shared")


def card(card_id, dataset, roles=None):
    item = {"id": card_id, "dataset": str(dataset.pk), "title": card_id, "chart_type": "number"}
    if roles is not None:
        item["allowed_roles"] = roles
    return item


class TestFilterLayout:
    def test_card_without_roles_visible_to_all(self, dataset, normal_user):
        layout = [card("c1", dataset, [])]
        assert filter_layout_for_user(layout, normal_user) == layout

    def test_card_filtered_by_role(self, dataset, normal_user):
        layout = [card("c1", dataset, ["card_role"]), card("c2", dataset, ["other_role"])]
        # 无任何命中角色 → 两张卡片都不可见（fail-closed）
        assert filter_layout_for_user(layout, normal_user) == []
        role = UserRole.objects.create(name="CARD", code="card_role", is_active=True)
        normal_user.roles.add(role)
        assert [item["id"] for item in filter_layout_for_user(layout, normal_user)] == ["c1"]

    def test_superuser_sees_all(self, dataset):
        admin = UserInfo.objects.create_superuser(
            username="card_admin", email="card_admin@example.com", password="Test@123456"
        )
        layout = [card("c1", dataset, ["some_role"]), card("c2", dataset, ["another_role"])]
        assert len(filter_layout_for_user(layout, admin)) == 2


class TestCardRoleValidation:
    def test_api_rejects_unknown_role_code(self, auth_client, dataset):
        resp = auth_client.post(
            DASHBOARDS_URL,
            {
                "name": f"卡片校验-{timezone.now().timestamp()}",
                "visibility": "personal",
                "layout": [card("c1", dataset, ["not_exist_role"])],
            },
            format="json",
        )
        assert resp.status_code == 400, resp.data

    def test_api_accepts_known_role_code(self, auth_client, dataset):
        UserRole.objects.create(name="CARD2", code="card_role2", is_active=True)
        resp = auth_client.post(
            DASHBOARDS_URL,
            {
                "name": f"卡片校验-{timezone.now().timestamp()}",
                "visibility": "personal",
                "layout": [card("c1", dataset, ["card_role2"])],
            },
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
