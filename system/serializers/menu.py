#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : menu
# author : ly_13
# date : 8/10/2024
from typing import Any
from urllib.parse import unquote, urlsplit

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.response.validation import ActiveUniqueValidationMixin
from common.core.serializers import BaseModelSerializer
from common.utils import get_logger
from system.models import Menu, MenuMeta

logger = get_logger(__name__)


class MenuMetaSerializer(BaseModelSerializer):
    class Meta:
        model = MenuMeta
        exclude = ["creator", "modifier", "id"]
        read_only_fields = ["creator", "modifier", "dept_belong", "id"]

    pk = serializers.UUIDField(source="id", read_only=True)

    def validate_frame_url(self, value: Any) -> str:
        """内嵌页只接受同源相对路径（同源相对路径）。

        绝对地址（`http(s)://`、`//host`）以及 `javascript:` / `data:` 都能把外部页面
        塞进可信外壳——用户在站内「正常页面」里输入凭据、或借壳触发站内操作。
        存量数据都是 `/api-docs/swagger/` 这类同源路径，收窄不会影响既有配置。
        """
        url = (value or "").strip()
        if not url:
            return ""
        # 反斜杠/控制字符会被浏览器归一为路径分隔符（`/\evil.com` → 外站），
        # 百分号编码的同形串同样拒绝——解码后再检查，最多展开三层编码
        decoded = url
        for _depth in range(3):
            next_value = unquote(decoded)
            if next_value == decoded:
                break
            decoded = next_value
        if "\\" in decoded or any(ord(char) < 0x20 or ord(char) == 0x7F for char in decoded):
            raise serializers.ValidationError(_("Iframe URL must be a same-origin path starting with /"))
        parts = urlsplit(url)
        if parts.scheme or parts.netloc or not decoded.startswith("/") or decoded.startswith("//"):
            raise serializers.ValidationError(_("Iframe URL must be a same-origin path starting with /"))
        return url


class MenuSerializer(ActiveUniqueValidationMixin, BaseModelSerializer):
    meta = MenuMetaSerializer(label=_("Menu meta"))

    class Meta:
        model = Menu
        fields = [
            "pk",
            "name",
            "rank",
            "path",
            "component",
            "meta",
            "parent",
            "menu_type",
            "is_active",
            "model",
            "method",
            "deleted_at",
        ]
        # read_only_fields = ['pk'] # 用于文件导入导出时，不丢失上级节点
        extra_kwargs = {
            "parent": {"attrs": ["pk", "name"], "allow_null": True, "required": False},
            "model": {"attrs": ["pk", "name", "label"], "allow_null": True, "required": False},
        }

    def validate_parent(self, value: Any) -> Any:
        """拒绝把菜单挂到自身或自己的后代下（parent 成环会让整棵子树消失）。"""
        if value is None or self.instance is None:
            return value
        current = value
        while current is not None:
            if current.pk == self.instance.pk:
                raise serializers.ValidationError(_("A menu cannot be its own parent or descendant"))
            current = current.parent
        return value

    # name 的 DB 唯一约束已改为"未删除数据"条件约束（见 Menu.Meta.constraints），
    # 显式校验活跃菜单唯一，保证重复时返回 400 而非数据库 IntegrityError
    def validate_name(self, value: Any) -> Any:
        # 菜单名对「未删除数据」唯一（软删模型），走共享 mixin 显式校验
        return self._validate_active_unique("name", value)

    def update(self, instance: Any, validated_data: Any) -> Any:
        with transaction.atomic():
            # meta 缺省时跳过：行内启停 / 批量启停只提交 is_active，不携带 meta，
            # 强制要求携带会让所有字段级局部更新（PATCH）直接 500
            meta_data = validated_data.pop("meta", None)
            if meta_data is not None:
                serializer = MenuMetaSerializer(instance.meta, data=meta_data, partial=True, context=self.context)
                serializer.is_valid(raise_exception=True)
                serializer.save()
            return super().update(instance, validated_data)

    def create(self, validated_data: Any) -> Any:
        with transaction.atomic():
            serializer = MenuMetaSerializer(data=validated_data.pop("meta"), context=self.context)
            serializer.is_valid(raise_exception=True)
            validated_data["meta"] = serializer.save()
            return super().create(validated_data)
