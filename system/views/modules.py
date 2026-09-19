#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modules
# author : ly_13
# date : 2026/09/17
"""功能模块清单与后台裁剪配置。

给「系统管理 → 模块管理」页提供数据：当前预设、每个模块的等级/依赖/覆盖范围/启停状态，
以及可直接粘贴到 config.yml 的裁剪配置片段（与 `manage.py modules` 同源）。

管理页可写入后台覆盖（DB 单行）：校验与启动期完全同口径（复用 `preview_modules`），
保存后**当前进程行为不变**，需重启进程生效——页面据此展示「待重启生效」差异。
裁剪语义、优先级与重启说明见 docs/architecture/模块化与功能裁剪.md。
"""

from django.core.exceptions import ImproperlyConfigured
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.viewsets import GenericViewSet

from common.core.modules import (
    PRESETS,
    clear_override,
    config_snippet,
    deployment_config,
    desired_modules,
    load_override,
    module_diff,
    modules_report,
    preset_module_ids,
    preview_modules,
    resolve_modules,
    save_override,
)
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
# 生效方式提示：标准部署为多容器，页面不提供进程内重启，由运维执行
RESTART_COMMAND = "sh xadmin.sh restart"
PRESET_LABELS = {
    "core": "仅内核（极简底座）",
    "standard": "内核 + 标配（推荐二次开发起点）",
    "full": "全部功能（默认）",
}


class ModuleApplySerializer(serializers.Serializer):
    """后台覆盖的写入入参（与 config.yml 的 MODULE_* 同语义）。"""

    preset = serializers.ChoiceField(choices=PRESETS)
    enable = serializers.ListField(child=serializers.CharField(), required=False)
    disable = serializers.ListField(child=serializers.CharField(), required=False)


def build_payload() -> dict:
    """管理页数据：生效态 + 待生效态 + 差异 + 部署基线。"""

    effective = resolve_modules()
    desired = desired_modules()
    diff = module_diff(effective, desired)
    override = load_override()
    baseline_preset, baseline_enable, baseline_disable = deployment_config()
    report = modules_report(effective)
    # 各预设的模块数：按预设纯口径计算（忽略当前的显式增删），供前端展示选择项；
    # module_ids 供前端按预设推导开关默认值，避免前端复制一份等级表
    presets = [
        {
            "value": preset,
            "label": PRESET_LABELS[preset],
            "enabled_count": len(preview_modules(preset=preset, enable=(), disable=()).enabled),
            "module_ids": sorted(preset_module_ids(preset)),
        }
        for preset in PRESETS
    ]
    return {
        # ---- 生效态（当前进程启动时解析的结果） ----
        "preset": effective.preset,
        "enabled_count": len(effective.enabled),
        "total": len(report),
        "disabled": sorted(effective.disabled),
        "presets": presets,
        "modules": report,
        "effective_config_snippet": config_snippet(effective),
        # ---- 待生效态（重启后生效；无覆盖行时与生效态一致） ----
        "config_snippet": config_snippet(desired),
        "desired": {
            "preset": desired.preset,
            "enabled_count": len(desired.enabled),
            "disabled": sorted(desired.disabled),
        },
        "pending": bool(diff["enable"] or diff["disable"] or diff["preset_changed"]),
        "diff": diff,
        "override_active": override is not None,
        "override_updated_time": override.updated_time if override else None,
        "baseline": {
            "preset": baseline_preset,
            "enable": list(baseline_enable),
            "disable": list(baseline_disable),
        },
        "restart_command": RESTART_COMMAND,
        "docs": MODULE_DOCS_PATH,
    }


class SystemModuleViewSet(GenericViewSet):
    """功能模块清单（可编辑后台裁剪配置）"""

    # 非模型视图：queryset 仅用于权限链与元数据机制，不参与查询
    queryset = Menu.objects.none()
    serializer_class = None
    ordering_fields = []

    @extend_schema(responses=modules_response_schema())
    def list(self, request, *args, **kwargs):
        """获取{cls}"""
        return ApiResponse(data=build_payload())

    @extend_schema(request=ModuleApplySerializer, responses=modules_response_schema())
    @action(detail=False, methods=["post"], url_path="apply")
    def apply(self, request, *args, **kwargs):
        """保存模块裁剪配置（重启后生效）

        校验与启动期完全同口径：未知模块 / 内核被关 / 依赖不满足直接 400。
        保存只落库，不改变当前进程的解析结果（避免等同于热更新）。
        """
        serializer = ModuleApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preset = serializer.validated_data["preset"]
        enable = serializer.validated_data.get("enable", [])
        disable = serializer.validated_data.get("disable", [])
        try:
            preview_modules(preset=preset, enable=enable, disable=disable)
        except ImproperlyConfigured as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        save_override(preset=preset, enable=enable, disable=disable)
        return ApiResponse(data=build_payload())

    @extend_schema(request=None, responses=modules_response_schema())
    @action(detail=False, methods=["post"], url_path="reset")
    def reset(self, request, *args, **kwargs):
        """恢复为部署配置（清除后台覆盖，重启后生效）"""
        clear_override()
        return ApiResponse(data=build_payload())
