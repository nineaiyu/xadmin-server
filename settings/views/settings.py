#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : settings
# author : ly_13
# date : 7/31/2024

from typing import Any

from django.conf import settings
from django_filters import rest_framework as filters

from common.core.filter import BaseFilterSet
from common.core.modelset import ImportExportDataAction, ListDeleteModelSet, NoDetailModelSet
from common.utils import get_logger
from settings.models import Setting
from settings.serializers.basic import BasicSettingSerializer
from settings.serializers.setting import SettingSerializer

logger = get_logger(__name__)


class BaseSettingViewSet(NoDetailModelSet):
    queryset = Setting.objects.all()
    serializer_class = BasicSettingSerializer
    category = "basic"
    serializer_class_mapper: dict[str, Any] = {}

    def get_serializer_class(self):
        if not self.serializer_class_mapper:
            return super().get_serializer_class()
        self.category = self.request.query_params.get("category", "basic")
        cls = self.serializer_class_mapper.get(self.category, self.serializer_class)
        return cls

    def metadata_extra_cache_key(self, request) -> str:
        """元数据字段面随 `?category=` 变化（get_serializer_class 收敛），并入缓存键。"""
        return str(self.request.query_params.get("category") or "")

    def get_fields(self):
        serializer = self.get_serializer_class()()
        fields = serializer.get_fields()
        return fields

    def get_object(self):
        items = self.get_fields().keys()
        obj = {}
        for item in items:
            if hasattr(settings, item):
                obj[item] = getattr(settings, item)
            else:
                obj[item] = None
        return obj

    def parse_serializer_data(self, serializer):
        data = []
        fields = self.get_fields()
        encrypted_items = [name for name, field in fields.items() if field.write_only]
        for name, value in serializer.validated_data.items():
            encrypted = name in encrypted_items
            if encrypted and value in ["", None]:
                continue
            data.append({"name": name, "value": value, "encrypted": encrypted, "category": self.category})
        return data

    def perform_update(self, serializer):
        """设置项保存（显式契约，配对 settings/serializers/contract.py）。

        - 仅 request.data 显式提交的键持久化（带 default 的可选字段未提交不落库，
          PUT 同口径——设置页语义是「改了什么存什么」）；write_only 密文提交
          空值 = 不修改（回退已存值）；
        - 响应载荷 = 未提交键取运行时当前值 + 变更键取新值的合并视图，经
          set_response_data 显式回写（不再裸改 serializer._data）；
        - serializer.change_fields = 实际落库且值变化的键名；
        - 序列化器可实现 post_save() 做联动失效/响应整形（change_fields 与响应
          载荷已就绪；运行时 settings 由 pub/sub 订阅者异步热更）。
        未经契约混入的序列化器（二开存量）维持旧 _data/_change_fields 写入一个
        版本周期。
        """
        post_data_names = set(self.request.data.keys())
        settings_items = self.parse_serializer_data(serializer)
        serializer_data = serializer.data
        change_fields = []
        for item in settings_items:
            if item["name"] not in post_data_names:
                continue
            changed, setting = Setting.update_or_create(**item, user=self.request.user)
            if not changed:
                continue
            change_fields.append(setting.name)
            serializer_data[setting.name] = setting.cleaned_value
        if hasattr(serializer, "set_response_data"):
            serializer.set_response_data(serializer_data)
            serializer.change_fields = change_fields
        else:
            # 未接入契约混入的存量序列化器：维持旧私有属性写入（下个版本周期移除）
            serializer._data = serializer_data
            serializer._change_fields = change_fields
        post_save = getattr(serializer, "post_save", None)
        if callable(post_save):
            post_save()


class SettingFilter(BaseFilterSet):
    pk = filters.UUIDFilter(field_name="id")
    name = filters.CharFilter(field_name="name", lookup_expr="icontains")
    value = filters.CharFilter(field_name="value", lookup_expr="icontains")

    class Meta:
        model = Setting
        fields = ["pk", "is_active", "name", "category", "value"]


class SettingViewSet(ListDeleteModelSet, ImportExportDataAction):
    """系统设置"""

    queryset = Setting.objects.all()
    serializer_class = SettingSerializer
    # 默认排序：模型无 Meta.ordering，缺省时 DRF 分页会抛 UnorderedObjectListWarning
    # 且跨页结果可能重复/丢失（orderng_fields 只放开 ?ordering= 参数，不提供默认值）
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "category"]
    filterset_class = SettingFilter
