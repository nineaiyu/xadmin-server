#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""列表视图序列化器。"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models import SavedListView


class SavedListViewSerializer(BaseModelSerializer):
    class Meta:
        model = SavedListView
        fields = [
            "pk",
            "owner",
            "page_key",
            "name",
            "conditions",
            "ordering",
            "is_default",
            "is_shared",
            "remark",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "owner", "created_time", "updated_time"]
        table_fields = ["name", "page_key", "is_default", "is_shared", "updated_time"]
        extra_kwargs = {"owner": {"attrs": ["pk", "username", "nickname"], "read_only": True, "format": "{nickname}"}}

    def validate(self, attrs: Any) -> Any:
        """owner 只读使 DRF 跳过唯一约束校验：显式查重返回 400 而不是 IntegrityError 500。"""
        instance = self.instance
        owner = instance.owner if instance is not None else self.context["request"].user
        name = attrs.get("name", instance.name if instance is not None else None)
        page_key = attrs.get("page_key", instance.page_key if instance is not None else None)
        if not name or not page_key:
            return attrs
        conflicts = SavedListView.objects.filter(owner=owner, page_key=page_key, name=name)
        if instance is not None:
            conflicts = conflicts.exclude(pk=instance.pk)
        if conflicts.exists():
            raise serializers.ValidationError({"name": _("A view with this name already exists for this page")})
        return attrs
