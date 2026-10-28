# -*- coding: utf-8 -*-
"""``build_queryset`` 的字段权限收口（NL 查数旁路封堵）。

``build_queryset`` 是 execute / NL 查数共用的取数内核：NL 通道不经
``execute_dataset`` 的二次裁剪，直接 ``values(*columns)`` 取数并落库——
内核不裁列就把字段权限旁路成「隐藏列原值直读」，请求级 filters 不按浏览者
收敛则可用 ``gte/lt`` 二分推断隐藏列分布。本文件钉住两个收口：
返回列按 ``viewer_visible_fields`` 裁剪、``extra_filters`` 带 ``user_obj`` 校验。
"""

import uuid

import pytest
from django.conf import settings as dj_settings
from django.core.exceptions import ValidationError

from dataset.models import Dataset
from dataset.utils.dataset import build_queryset
from system.models import FieldPermission, Menu, MenuMeta, ModelLabelField

pytestmark = pytest.mark.django_db

USER_BOUND = "identity.userinfo"


@pytest.fixture(autouse=True)
def _field_permission_on(settings):
    dj_settings.PERMISSION_FIELD_ENABLED = True


@pytest.fixture
def user_registry(db):
    root, _ = ModelLabelField.objects.get_or_create(
        name=USER_BOUND,
        defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": "用户"},
    )
    for name in ("username", "nickname", "phone"):
        ModelLabelField.objects.get_or_create(
            name=name,
            parent=root,
            defaults={"field_type": ModelLabelField.FieldChoices.DATA, "label": name},
        )
    return root


@pytest.fixture
def dataset_with_hidden_column(user_registry, superuser):
    """数据集列包含敏感列 phone：浏览者无其字段权限时内核必须裁掉。"""
    return Dataset.objects.create(
        name=f"NL字段权限数据集-{uuid.uuid4().hex[:6]}",
        bound_model=USER_BOUND,
        columns=["username", "phone"],
        filters=[],
        row_limit=100,
        visibility="shared",
        creator=superuser,
    )


@pytest.fixture
def restricted_viewer(normal_user, user_registry):
    """只授予 username 字段权限的普通用户（字段权限行挂在自建菜单下）。"""
    menu = Menu.objects.create(
        name="execute:Dataset",
        path="api/dataset/datasets$",
        method="GET",
        menu_type=Menu.MenuChoices.PERMISSION,
        meta=MenuMeta.objects.create(title="execute:Dataset"),
    )
    root = ModelLabelField.objects.get(name="username", parent__name=USER_BOUND)
    permission = FieldPermission.objects.create(role=normal_user.roles.first(), menu=menu)
    permission.field.add(root)
    return normal_user


def test_build_queryset_trims_hidden_columns(restricted_viewer, dataset_with_hidden_column):
    """无 phone 字段权限的浏览者：返回列与行数据都不得包含 phone。"""
    queryset, _model, columns = build_queryset(dataset_with_hidden_column, restricted_viewer)
    assert columns == ["username"]
    rows = list(queryset.values(*columns))
    assert rows == [] or set(rows[0].keys()) == {"username"}


def test_build_queryset_rejects_filter_on_hidden_column(restricted_viewer, dataset_with_hidden_column):
    """隐藏列不得作为请求级过滤条件（阻断 gte/lt 二分推断）。"""
    with pytest.raises(ValidationError):
        build_queryset(
            dataset_with_hidden_column,
            restricted_viewer,
            extra_filters=[{"field": "phone", "op": "gte", "value": "1"}],
        )


def test_build_queryset_allows_filter_on_visible_column(restricted_viewer, dataset_with_hidden_column):
    queryset, _model, _columns = build_queryset(
        dataset_with_hidden_column,
        restricted_viewer,
        extra_filters=[{"field": "username", "op": "contains", "value": "a"}],
    )
    assert queryset.query.has_filters()


def test_build_queryset_no_field_config_returns_all_columns(superuser, dataset_with_hidden_column):
    """浏览者无任何字段权限配置：不裁剪（字段权限是显式授权行为，见 viewer_visible_fields）。"""
    _queryset, _model, columns = build_queryset(dataset_with_hidden_column, superuser)
    assert columns == ["username", "phone"]
