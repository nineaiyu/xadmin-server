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

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.viewsets import GenericViewSet

from common.cache.lock import ReentrantLock
from common.core.config import SysConfig
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


def modules_response_schema() -> Any:
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
# 生效方式提示：标准部署为多容器，页面不提供进程内重启，由运维执行；
# 命令按部署形态可配（SysConfig MODULE_RESTART_COMMAND，Docker/Compose 部署覆盖）
PRESET_LABELS = {
    "core": _("Core modules only (minimal base)"),
    "standard": _("Core + standard modules (recommended for secondary development)"),
    "full": _("All modules (default)"),
}

# 后台覆盖写入互斥（apply / reset 共用一把锁）：「校验 → 落库 → 回显」整段串行化，
# 否则并发请求会交错——后写者无声覆盖前者的校验结论，回显也可能混入对方的落库结果。
# 保存是单行秒级写入，竞争方快速失败比排队等待更符合管理页交互；
# TTL 为进程异常终止时的兜底释放，正常路径请求结束即释放。
OVERRIDE_WRITE_LOCK_NAME = "modules_override_write"
OVERRIDE_WRITE_LOCK_TTL = 10  # 秒


def _acquire_override_write_lock() -> ReentrantLock:
    """获取覆盖写入互斥锁；拿不到立即以 400 失败（不排队等待）。"""

    lock = ReentrantLock(OVERRIDE_WRITE_LOCK_NAME, timeout=OVERRIDE_WRITE_LOCK_TTL)
    if not lock.acquire(blocking=False):
        raise ValidationError({"detail": _("Another module configuration save is in progress, please try again later")})
    return lock


class ModuleApplySerializer(serializers.Serializer):
    """后台覆盖的写入入参（与 config.yml 的 MODULE_* 同语义）。"""

    preset = serializers.ChoiceField(choices=PRESETS)
    enable = serializers.ListField(child=serializers.CharField(), required=False)
    disable = serializers.ListField(child=serializers.CharField(), required=False)


def build_payload() -> dict[str, Any]:
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
        "restart_command": SysConfig.MODULE_RESTART_COMMAND,
        "docs": MODULE_DOCS_PATH,
    }


class SystemModuleViewSet(GenericViewSet):
    """功能模块清单（可编辑后台裁剪配置）"""

    # 非模型视图：queryset 仅用于权限链与元数据机制，不参与查询
    queryset = Menu.objects.none()
    serializer_class = None
    ordering_fields: list[str] = []

    @extend_schema(responses=modules_response_schema())
    def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取{cls}"""
        return ApiResponse(data=build_payload())

    @extend_schema(request=ModuleApplySerializer, responses=modules_response_schema())
    @action(detail=False, methods=["post"], url_path="apply")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def apply(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """保存模块裁剪配置（重启后生效）

        校验与启动期完全同口径：未知模块 / 内核被关 / 依赖不满足直接 400。
        保存只落库，不改变当前进程的解析结果（避免等同于热更新）。
        校验、落库与回显在写入锁内整体完成，并发保存互斥串行。
        """
        serializer = ModuleApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preset = serializer.validated_data["preset"]
        enable = serializer.validated_data.get("enable", [])
        disable = serializer.validated_data.get("disable", [])
        lock = _acquire_override_write_lock()
        try:
            # 校验必须与落库同锁：校验结论对「即将写入的这份配置」负责，
            # 锁外校验会让并发写入插进校验与落库之间，失去互斥意义
            try:
                preview_modules(preset=preset, enable=enable, disable=disable)
            except ImproperlyConfigured as exc:
                raise ValidationError({"detail": str(exc)}) from exc
            save_override(preset=preset, enable=enable, disable=disable)
            payload = build_payload()
        finally:
            lock.release()
        return ApiResponse(data=payload)

    @extend_schema(request=None, responses=modules_response_schema())
    @action(detail=False, methods=["post"], url_path="reset")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def reset(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """恢复为部署配置（清除后台覆盖，重启后生效）"""
        lock = _acquire_override_write_lock()
        try:
            clear_override()
            payload = build_payload()
        finally:
            lock.release()
        return ApiResponse(data=payload)
