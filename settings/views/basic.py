#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : basic
# author : ly_13
# date : 7/31/2024

from typing import Any

from common.core.permission_meta import shared_permissions
from common.utils import get_logger
from settings.serializers.basic import BasicSettingSerializer
from settings.views.settings import BaseSettingViewSet

logger = get_logger(__name__)

WATERMARK_FIELD_PREFIX = "FRONT_END_WEB_WATERMARK_"


def _basic_setting_field_groups(view: Any) -> dict[str, set[str]]:
    """基本/水印两个权限码共用 PATCH 端点，按提交字段隔离。

    水印字段按前缀归组，其余字段归基本设置；新增字段无需同步维护清单。
    读取（GET）保持 OR 语义（任一 retrieve 码可读全部设置字段）：设置项非敏感值，
    读隔离需让元数据/搜索列缓存按角色分片，收益低于复杂度（产品决策，测试钉住）。
    """
    fields = set(view.get_fields())
    watermark = {name for name in fields if name.startswith(WATERMARK_FIELD_PREFIX)}
    return {
        "partialUpdate:SettingBasic": fields - watermark,
        "partialUpdate:SettingWatermark": watermark,
    }


@shared_permissions(
    path="api/settings/basic",
    methods={
        "GET": ("retrieve:SettingBasic", "retrieve:SettingWatermark"),
        "PATCH": ("partialUpdate:SettingBasic", "partialUpdate:SettingWatermark"),
    },
    field_groups={"PATCH": _basic_setting_field_groups},
)
class BasicSettingViewSet(BaseSettingViewSet):
    """基本设置"""

    serializer_class = BasicSettingSerializer
    category = "basic"
