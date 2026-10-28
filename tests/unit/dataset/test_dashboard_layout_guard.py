# -*- coding: utf-8 -*-
"""仪表盘 layout 保存校验：上限档位 + 可见数据集引用 + 入库形状收敛。

layout 无上限时卡片数 / 标题长度 / span / height / id 与角色配置全靠前端自觉；
数据集存在性用全表判定则让低权用户可借「Unknown dataset」差异探测他人
personal 数据集。保存者可见域走 ``visible_dataset_queryset``（personal 仅创建者）。
"""

import uuid

import pytest
from rest_framework.test import APIClient

from dataset.models import Dashboard, Dataset
from dataset.serializers.dataset import DashboardSerializer

pytestmark = pytest.mark.django_db

DASHBOARDS_URL = "/api/dataset/dashboards"


def _dataset(name, creator, visibility):
    return Dataset.objects.create(
        name=name,
        bound_model="demo.book",
        columns=["name"],
        filters=[],
        row_limit=10,
        visibility=visibility,
        creator=creator,
    )


def _card(dataset_pk, **extra):
    card = {"id": f"c-{uuid.uuid4().hex[:6]}", "dataset": str(dataset_pk), "chart_type": "number"}
    card.update(extra)
    return card


def _save(api_client, layout):
    client = APIClient()
    if not getattr(api_client, "is_superuser", False):
        _grant_create(api_client)
    client.force_authenticate(api_client)
    return client.post(
        DASHBOARDS_URL,
        {"name": f"仪表盘-{uuid.uuid4().hex[:6]}", "layout": layout, "visibility": "personal"},
        format="json",
    )


def _grant_create(user):
    """普通用户补仪表盘 create 权限点（本文件只验证 layout 校验面）。"""
    from django.core.cache import cache as django_cache

    from system.models import Menu, MenuMeta

    menu, _ = Menu.objects.get_or_create(
        name="create:DataDashboard",
        path="api/dataset/dashboards$",
        method="POST",
        menu_type=Menu.MenuChoices.PERMISSION,
        defaults={"meta": MenuMeta.objects.create(title="create:DataDashboard")},
    )
    role = user.roles.first()
    if role is not None:
        role.menu.add(menu)
    django_cache.clear()


def test_layout_rejects_other_users_personal_dataset(normal_user, superuser):
    """引用他人 personal 数据集必须拒绝（存在性探测与越权引用一并收口）。"""
    hidden = _dataset("他人个人集", superuser, "personal")
    resp = _save(normal_user, [_card(hidden.pk)])
    assert resp.status_code == 400


def test_layout_accepts_shared_dataset(normal_user, superuser):
    shared = _dataset("共享集", superuser, "shared")
    resp = _save(normal_user, [_card(shared.pk)])
    assert resp.status_code in (200, 201)


def test_layout_rejects_over_card_cap(superuser):
    from dataset.serializers.dataset import DASHBOARD_MAX_CARDS

    shared = _dataset("容量集", superuser, "shared")
    resp = _save(superuser, [_card(shared.pk) for _ in range(DASHBOARD_MAX_CARDS + 1)])
    assert resp.status_code == 400


def test_layout_rejects_bad_title_and_span(superuser):
    shared = _dataset("字段集", superuser, "shared")
    resp = _save(superuser, [_card(shared.pk, title="长" * 65)])
    assert resp.status_code == 400
    resp = _save(superuser, [_card(shared.pk, span=7)])
    assert resp.status_code == 400


def test_serializer_context_free_save_keeps_full_existence(superuser):
    """无请求上下文的内部调用（无浏览者可收敛）维持全量存在性，不误伤程序化创建。"""
    personal = _dataset("内部个人集", superuser, "personal")
    serializer = DashboardSerializer(
        data={"name": "内部盘", "layout": [_card(personal.pk)], "visibility": "personal"},
        context={},
    )
    assert serializer.is_valid(), serializer.errors


def test_layout_height_restricted_to_frontend_tiers(superuser):
    """height 与前端 cardHeight.ts 档位对齐：档位内放行，档位外（含类型不符）拒绝。"""
    shared = _dataset("高度集", superuser, "shared")
    resp = _save(superuser, [_card(shared.pk, height=320)])
    assert resp.status_code in (200, 201), resp.data
    for bad_height in (300, "224"):
        resp = _save(superuser, [_card(shared.pk, height=bad_height)])
        assert resp.status_code == 400, bad_height


def test_layout_strips_unknown_card_keys(superuser):
    """契约外键静默剔除：入库卡片键集合与前端 DashboardCard 契约一致（不做超集）。"""
    shared = _dataset("纯净集", superuser, "shared")
    card = _card(shared.pk, title="纯净卡", height=224, secret_field="x", nested={"a": 1})
    resp = _save(superuser, [card])
    assert resp.status_code in (200, 201), resp.data
    stored = Dashboard.objects.get(pk=resp.data["data"]["pk"]).layout
    assert len(stored) == 1
    assert set(stored[0]) == {"id", "dataset", "title", "chart_type", "height"}


def test_layout_dedupes_allowed_roles(superuser):
    """allowed_roles 重复角色码按首次出现顺序去重后入库。"""
    from identity.models import UserRole

    shared = _dataset("去重集", superuser, "shared")
    UserRole.objects.create(name="审计查看", code="audit-viewer")
    UserRole.objects.create(name="运维查看", code="ops-viewer")
    card = _card(shared.pk, allowed_roles=["audit-viewer", "audit-viewer", "ops-viewer"])
    resp = _save(superuser, [card])
    assert resp.status_code in (200, 201), resp.data
    stored = Dashboard.objects.get(pk=resp.data["data"]["pk"]).layout
    assert stored[0]["allowed_roles"] == ["audit-viewer", "ops-viewer"]


def test_layout_rejects_overlong_card_id(superuser):
    """卡片 id 上限 64 字符：超长拒绝，边界值放行。"""
    shared = _dataset("编号集", superuser, "shared")
    resp = _save(superuser, [_card(shared.pk, id="c" * 65)])
    assert resp.status_code == 400
    resp = _save(superuser, [_card(shared.pk, id="c" * 64)])
    assert resp.status_code in (200, 201), resp.data
