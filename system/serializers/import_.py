#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""异步导入记录（下载中心）序列化器。

只读资源：全部字段 read_only；列表展示文件名/目标模块/动作/状态/行数统计/报告/触发人。
状态走数据字典 import_status（管理员可维护文案/颜色），未配置回退模型枚举。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from system.models.import_ import ImportRecord, ImportTemplate
from system.serializers.fields import DictChoiceField
from system.serializers.task import DisplayRelatedField


class ImportRecordSerializer(BaseModelSerializer):
    source_file = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Source file"), label_builder=lambda value: value.filename
    )
    error_report = DisplayRelatedField(
        read_only=True, allow_null=True, label=_("Error report"), label_builder=lambda value: value.filename
    )
    creator = DisplayRelatedField(read_only=True, allow_null=True, label=_("Creator"))
    report_filesize = serializers.SerializerMethodField(label=_("Report size"))
    # 运行期进度走缓存通道（任务在外层事务里执行，库内字段提交前其他连接读不到），
    # 终态仍读模型字段（任务收尾会写 100 并清理缓存）
    progress = serializers.SerializerMethodField(label=_("Progress"))
    # 状态走数据字典 import_status（管理员可维护文案/颜色，默认项随种子下发）；
    # 未配置回退模型枚举，merge 保证只配部分项时其余枚举标签不缺
    status = DictChoiceField(
        dict_code="import_status",
        fallback_choices=ImportRecord.Status.choices,
        merge_fallback=True,
        read_only=True,
    )
    # 导入动作（create/update）同样字典化（import_action）：列表彩色 tag 与文案可运营
    action = DictChoiceField(
        dict_code="import_action",
        fallback_choices=ImportRecord.Action.choices,
        merge_fallback=True,
        read_only=True,
    )

    class Meta:
        model = ImportRecord
        fields = [
            "pk",
            "name",
            "module",
            "path",
            "action",
            "status",
            "progress",
            "total",
            "success_rows",
            "failed_rows",
            "source_file",
            "error_report",
            "report_filesize",
            "creator",
            "error",
            "created_time",
            "updated_time",
        ]
        table_fields = [
            "pk",
            "name",
            "module",
            "action",
            "status",
            "progress",
            "total",
            "success_rows",
            "failed_rows",
            "report_filesize",
            "error_report",
            "creator",
            "created_time",
        ]
        read_only_fields = fields

    def get_report_filesize(self, obj):
        return obj.report_filesize

    def get_progress(self, obj):
        if obj.status == ImportRecord.Status.RUNNING:
            from system.utils.import_progress import get_import_progress

            cached = get_import_progress(obj.pk)
            if cached is not None:
                return cached
        return obj.progress


class ImportTemplateSerializer(BaseModelSerializer):
    """导入列映射模板：mapping/options 形态清洗 + 共享模板仅超管可维护。

    ``mapping`` 的目标字段合法性不在此处校验：字段随目标模型而定，解释权在
    导入链路的 ``common/core/import_mapping.build_field_index``（未知目标按未匹配处理）。
    """

    MAPPING_MAX_ITEMS = 200
    OPTIONS_KEYS = ("ignore_unknown",)

    class Meta:
        model = ImportTemplate
        fields = [
            "pk",
            "model",
            "name",
            "mapping",
            "options",
            "is_shared",
            "creator",
            "created_time",
            "updated_time",
        ]
        table_fields = ["pk", "model", "name", "is_shared", "creator", "created_time"]
        read_only_fields = ["creator"]

    def get_unique_together_validators(self):
        """禁用 DRF 对 (model, name, creator) 约束生成的 UniqueTogetherValidator。

        creator 由框架按登录用户自动赋值（只读），DRF 会把可空的 creator 误判为
        必填；唯一性由 validate() 显式查重，DB 唯一约束兜底并发。
        """
        return []

    @staticmethod
    def _is_superuser(request):
        return bool(getattr(getattr(request, "user", None), "is_superuser", False))

    def validate_mapping(self, value):
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError(_("Invalid import mapping"))
        if len(value) > self.MAPPING_MAX_ITEMS:
            raise serializers.ValidationError(_("Too many columns in mapping (max {})").format(self.MAPPING_MAX_ITEMS))
        cleaned = {}
        for key, target in value.items():
            if not isinstance(key, str) or not key.strip():
                raise serializers.ValidationError(_("Invalid import mapping"))
            cleaned[key.strip()] = "" if target is None else str(target).strip()
        return cleaned

    def validate_options(self, value):
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError(_("Operation failed. Abnormal data"))
        return {key: value[key] for key in self.OPTIONS_KEYS if key in value}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        if not self._is_superuser(request):
            # 共享模板对普通用户只读：既不能新建/改为共享，也不能改/删既有共享模板
            if attrs.get("is_shared") or (self.instance and self.instance.is_shared):
                raise serializers.ValidationError({"is_shared": _("Only superuser can manage shared templates")})
        model = attrs.get("model") or getattr(self.instance, "model", None)
        name = attrs.get("name") or getattr(self.instance, "name", None)
        if model and name:
            queryset = ImportTemplate.objects.filter(model=model, name=name)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            else:
                queryset = queryset.filter(creator=getattr(request, "user", None))
            if queryset.exists():
                raise serializers.ValidationError({"name": _("Template name already exists")})
        return attrs
