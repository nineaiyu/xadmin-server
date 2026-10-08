#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通用 DRF 校验工具（跨 app 复用）：活跃唯一校验 mixin 与必填修剪 helper。"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class ActiveUniqueValidationMixin:
    """「未删除数据」条件唯一校验（软删模型序列化器复用）。

    名称/编码的唯一性为 ``Meta.constraints`` 的活跃行条件约束：DRF 不会为带
    condition 的 UniqueConstraint 自动生成校验器，此处显式校验保证重复时返回
    400 而非数据库 IntegrityError；软删除行不占用名称/编码。

    使用：混入序列化器并提供 ``Meta.model``（默认管理器即活跃行口径）。
    """

    # 宿主序列化器提供（DRF ModelSerializer 契约）。
    Meta: Any
    instance: Any

    def _validate_active_unique(self, field_name: str, value: Any) -> Any:
        queryset = self.Meta.model.objects.filter(**{field_name: value})
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError(_("This field already exists"))
        return value


def trim_required(value: Any, message: Any) -> str:
    """去除首尾空白并拒绝空值（None / 全空白）。

    翻译文案由调用点以 ``_()`` 传入（保证 makemessages 静态提取）。
    """
    trimmed = (value or "").strip()
    if not trimmed:
        raise serializers.ValidationError(message)
    return trimmed
