# -*- coding: utf-8 -*-
"""设置端点 ?category= 取值域校验。

带 serializer_class_mapper 的设置视图集把 category 直接写入 Setting 分区；
未校验时拼错/伪造的 category 会静默落到新分区（读侧永远查不到）。
"""

import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from settings.serializers.basic import BasicSettingSerializer
from settings.views.settings import BaseSettingViewSet


class _MappedSettingViewSet(BaseSettingViewSet):
    category = "alpha"
    serializer_class = BasicSettingSerializer
    serializer_class_mapper = {"alpha": BasicSettingSerializer}


def _view(query: str) -> _MappedSettingViewSet:
    request = APIRequestFactory().get(f"/api/settings/demo/{query}")
    view = _MappedSettingViewSet()
    view.request = Request(request)
    return view


def test_unknown_category_rejected():
    with pytest.raises(ValidationError):
        _view("?category=beta").get_serializer_class()


def test_known_category_uses_mapper():
    assert _view("?category=alpha").get_serializer_class() is BasicSettingSerializer


def test_default_category_without_query():
    assert _view("").get_serializer_class() is BasicSettingSerializer
