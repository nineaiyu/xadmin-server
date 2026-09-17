#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modules
# author : ly_13
# date : 2026/09/17
"""功能模块清单（只读）。

给「系统管理 → 模块管理」页提供数据：当前预设、每个模块的等级/依赖/覆盖范围/启停状态，
以及可直接粘贴到 config.yml 的裁剪配置片段（与 `manage.py modules` 同源）。
裁剪本身通过 config.yml + 重启生效，本接口不提供写操作（见
docs/architecture/模块化与功能裁剪.md）。
"""

from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.viewsets import GenericViewSet

from common.core.modules import PRESETS, config_snippet, modules_report, preview_modules, resolve_modules
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models import Menu


def modules_response_schema():
    return get_default_response_schema(
        {
            "preset": build_basic_type(OpenApiTypes.STR),
            "enabled_count": build_basic_type(OpenApiTypes.INT),
            "total": build_basic_type(OpenApiTypes.INT),
            "modules": build_object_type(),
            "presets": build_object_type(),
            "config_snippet": build_basic_type(OpenApiTypes.STR),
            "docs": build_basic_type(OpenApiTypes.STR),
        }
    )


# 裁剪语义文档（前端展示的「怎么用」提示）
MODULE_DOCS_PATH = "docs/architecture/模块化与功能裁剪.md"
PRESET_LABELS = {
    "core": "仅内核（极简底座）",
    "standard": "内核 + 标配（推荐二次开发起点）",
    "full": "全部功能（默认）",
}


class SystemModuleViewSet(GenericViewSet):
    """功能模块清单（只读）"""

    # 非模型视图：queryset 仅用于权限链与元数据机制，不参与查询
    queryset = Menu.objects.none()
    serializer_class = None
    ordering_fields = []

    @extend_schema(responses=modules_response_schema())
    def list(self, request, *args, **kwargs):
        """获取{cls}"""
        resolution = resolve_modules()
        report = modules_report(resolution)
        # 各预设的模块数：按预设纯口径计算（忽略当前的显式增删），供前端展示选择项
        presets = [
            {
                "value": preset,
                "label": PRESET_LABELS[preset],
                "enabled_count": len(preview_modules(preset=preset, enable=(), disable=()).enabled),
            }
            for preset in PRESETS
        ]
        return ApiResponse(
            data={
                "preset": resolution.preset,
                "enabled_count": len(resolution.enabled),
                "total": len(report),
                "disabled": sorted(resolution.disabled),
                "presets": presets,
                "modules": report,
                "config_snippet": config_snippet(resolution),
                "docs": MODULE_DOCS_PATH,
            }
        )
