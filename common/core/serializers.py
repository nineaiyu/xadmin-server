#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializers
# author : ly_13
# date : 12/21/2023
from inspect import isfunction
from typing import List

from django.conf import settings
from django.db.models import QuerySet
from django.db.models.fields import NOT_PROVIDED
from rest_framework.fields import empty
from rest_framework.request import Request
from rest_framework.serializers import ModelSerializer

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from common.utils import get_logger
from server.utils import get_current_request

logger = get_logger(__name__)


class BaseModelSerializer(ModelSerializer):
    serializer_related_field = BasePrimaryKeyRelatedField
    serializer_choice_field = LabeledChoiceField
    ignore_field_permission = False  # 忽略字段权限

    class Meta:
        model = None
        table_fields = []  # 用于控制前端table的字段展示
        tabs = []

    def get_field_names(self, declared_fields, info):
        """将默认的id字段 转换为 pk"""
        fields = super().get_field_names(declared_fields, info)
        if "id" in fields:
            return ["pk"] + [f for f in fields if f != "id"]
        return fields

    def get_value(self, dictionary):
        # We override the default field access in order to support
        # nested HTML forms.
        # 下面两行注释是因为已经在前面处理过form-data，这里无需再次处理
        # if html.is_html_input(dictionary):
        #     return html.parse_html_dict(dictionary, prefix=self.field_name) or empty
        return dictionary.get(self.field_name, empty)

    def get_allow_fields(self, fields, ignore_field_permission):
        """
        self.fields: 默认定义的字段
        fields: 需要展示的字段
        allow_fields: 字段权限允许的字段
        """
        _fields = set(self.fields)
        if fields is None:
            fields = _fields

        if (
            self.ignore_field_permission
            or ignore_field_permission
            or getattr(self.request, "ignore_field_permission", False)
        ):
            return set(fields) & _fields

        allow_fields = []
        # 获取权限字段，如果没有配置，则为定义的所有字段
        if self.request and settings.PERMISSION_FIELD_ENABLED and not self.ignore_field_permission:
            if hasattr(self.request, "user") and self.request.user and self.request.user.is_superuser:
                allow_fields = _fields
            elif hasattr(self.request, "fields"):
                if self.request.fields and isinstance(self.request.fields, dict):
                    allow_fields = self.request.fields.get(self.Meta.model._meta.label_lower, [])
        else:
            allow_fields = _fields

        return set(fields) & _fields & set(allow_fields)

    def __init__(self, instance=None, data=empty, fields=None, ignore_field_permission=False, **kwargs):
        """
        :param instance:
        :param data:
        :param request: Request 对象
        :param fields: 序列化展示的字段， 默认定义的全部字段
        :param ignore_field_permission: 忽略字段权限控制
        """
        super().__init__(instance, data, **kwargs)
        meta = getattr(self, "Meta", None)
        if meta and hasattr(meta, "tabs") and meta.fields != "__all__":
            meta.fields = meta.fields + self.get_fields_from_tabs(meta.tabs)

        self.request: Request = get_current_request()
        if self.request is None:
            return
        # 记录显式豁免参数，供输出侧（to_representation 脱敏）与 get_allow_fields 同口径判断
        self.ignore_field_permission = self.ignore_field_permission or ignore_field_permission
        allowed = self.get_allow_fields(fields, ignore_field_permission)
        for field_name in set(self.fields) - allowed:
            self.fields.pop(field_name)

    @staticmethod
    def get_fields_from_tabs(tabs: List) -> List[str]:
        seen = set()
        result = []
        for tab in tabs:
            for field in tab.fields:
                if field not in seen:
                    seen.add(field)
                    result.append(field)
        return result

    def get_page_instances(self, default=None):
        """
        获取当前正在序列化的整页对象列表（many=True 时为分页结果），供 SerializerMethodField
        做整页批量查询；整页对象与当前对象类型不一致（嵌套序列化场景）或单对象序列化时，
        退化为 [default]，保持与逐对象查询相同的行为。
        """
        instances = getattr(getattr(self, "parent", None), "instance", None)
        if (
            isinstance(instances, (list, tuple))
            and instances
            and all(isinstance(instance, default.__class__) for instance in instances)
        ):
            return instances
        return [default] if default is not None else []

    def build_standard_field(self, field_name, model_field):
        field_class, field_kwargs = super().build_standard_field(field_name, model_field)
        default = getattr(model_field, "default", NOT_PROVIDED)
        if default != NOT_PROVIDED:
            # 将model中的默认值同步到序列化中
            if isfunction(default):
                default = default()
            field_kwargs.setdefault("default", default)
        return field_class, field_kwargs

    def create(self, validated_data):
        n_file_objs = []
        for field in self.Meta.model._meta.get_fields():
            if field.is_relation and field.related_model._meta.label == "system.UploadFile":
                if field.name in validated_data:
                    file_data = validated_data[field.name]
                    if isinstance(file_data, (list, QuerySet)):
                        n_file_objs.extend(validated_data.get(field.name))
                    else:
                        n_file_objs.append(validated_data.get(field.name))

        result = super().create(validated_data)

        for n_file in n_file_objs:
            setattr(n_file, "is_tmp", False)
            n_file.save(update_fields=["is_tmp"])
        return result

    def update(self, instance, validated_data):
        n_file_objs = []
        d_file_objs = []
        for field in self.Meta.model._meta.get_fields():
            if field.is_relation and field.related_model._meta.label == "system.UploadFile":
                if field.name in validated_data:
                    file_data = validated_data[field.name]
                    if isinstance(file_data, (list, QuerySet)):
                        d_file_objs.extend(
                            set(getattr(instance, field.name).all()) - set(validated_data.get(field.name))
                        )
                        n_file_objs.extend(
                            set(validated_data.get(field.name)) - set(getattr(instance, field.name).all())
                        )
                    else:
                        o_file_obj = getattr(instance, field.name)
                        n_file_obj = validated_data.get(field.name)
                        if o_file_obj.pk != n_file_obj.pk:
                            d_file_objs.append(o_file_obj)
                            n_file_objs.append(n_file_obj)

        result = super().update(instance, validated_data)

        for d_file in d_file_objs:
            d_file.delete()
        for n_file in n_file_objs:
            setattr(n_file, "is_tmp", False)
            n_file.save(update_fields=["is_tmp"])
        return result

    def _mask_exempt(self, request, user):
        """脱敏豁免判定（与 get_allow_fields 同口径）：超管 / 显式豁免 / 原文通道。

        原文通道 = 显式 ``?mask=false`` 且当前用户对该菜单有更新权限。编辑弹窗依赖
        列表行数据，若拿不到原文则会把掩码值回写（to_internal_value 另有兜底守护）；
        仅有更新权限的用户才被放行，只读用户的列表/详情/导出仍按规则掩码。
        """
        if user.is_superuser or self.ignore_field_permission or getattr(request, "ignore_field_permission", False):
            return True
        params = getattr(request, "query_params", None) or getattr(request, "GET", None)
        if not params:
            return False
        if str(params.get("mask", "")).lower() not in ("false", "0", "no"):
            return False
        cached = getattr(request, "_mask_original_allowed", None)
        if cached is None:
            # 惰性 import：common 层不引 system（跨 app 门禁许可函数内惰性 import）
            from common.core.permission import user_can_update_menu

            cached = user_can_update_menu(user, getattr(user, "menu", None))
            try:
                request._mask_original_allowed = cached
            except AttributeError:  # 只读请求对象兜底
                pass
        return cached

    def _mask_role_pks(self, request, user):
        """当前用户角色 pk 集合（按请求缓存，避免列表逐行 N+1 查询）。"""
        cached = getattr(request, "_mask_role_pks", None)
        if cached is not None:
            return cached
        role_pks = set()
        if hasattr(user, "roles"):
            try:
                role_pks = set(user.roles.values_list("pk", flat=True))
            except Exception:  # noqa: BLE001 非 UserInfo 用户（Anonymous 等）兜底
                role_pks = set()
        try:
            request._mask_role_pks = role_pks
        except AttributeError:  # 只读请求对象兜底
            pass
        return role_pks

    def to_representation(self, instance):
        """字段级数据脱敏钩子：列表/详情/导出同一条输出链路统一掩码。

        豁免口径与 get_allow_fields 一致（超管 / ignore_field_permission / 原文通道）；
        规则按模型 label_lower 缓存加载，命中则替换字段输出值——只脱敏输出，写入不受
        影响（写入侧的掩码回写守护在 to_internal_value）。
        """
        ret = super().to_representation(instance)
        model = getattr(getattr(self, "Meta", None), "model", None)
        request = self.request
        if model is None or request is None:
            return ret
        user = getattr(request, "user", None)
        if user is None or not hasattr(user, "is_superuser"):
            return ret
        if self._mask_exempt(request, user):
            return ret
        # 惰性 import：common 层不引 system（跨 app 门禁许可函数内惰性 import）
        from system.utils.mask import apply_mask, get_mask_rules

        rules = get_mask_rules(model._meta.label_lower)
        if not rules:
            return ret
        role_pks = self._mask_role_pks(request, user)
        # 规则按 sort 升序返回；同字段「sort 小者优先」——首个命中（字段匹配 + 角色匹配）
        # 的规则生效，其后该字段规则跳过；不同字段互不干扰
        masked_fields = set()
        for rule in rules:
            field_name = rule["field"]
            if field_name in masked_fields or field_name not in ret:
                continue
            if rule["roles"] and not bool(role_pks & set(rule["roles"])):
                continue
            ret[field_name] = apply_mask(ret[field_name], rule)
            masked_fields.add(field_name)
        return ret

    def to_internal_value(self, data):
        """写入侧统一入口：丢弃「掩码回写」的字段值（数据完整性守护）。

        编辑弹窗的初始值来自被掩码的输出，用户未修改该字段时会把 ``138******78``
        原样提交；这里与库内原文比对（等于原文的掩码结果则丢弃），避免原文被掩码串
        覆盖（不可逆）。真正的改动（提交值 != 掩码结果）照常写入。
        """
        ret = super().to_internal_value(data)
        self._drop_masked_writeback(ret)
        return ret

    def _drop_masked_writeback(self, ret):
        instance = self.instance
        model = getattr(getattr(self, "Meta", None), "model", None)
        if instance is None or not ret or model is None:
            return
        from system.utils.mask import apply_mask, get_mask_rules

        rules = get_mask_rules(model._meta.label_lower)
        if not rules:
            return
        rule_map = {}
        for rule in rules:
            rule_map.setdefault(rule["field"], rule)
        for name in list(ret):
            rule = rule_map.get(name)
            field = self.fields.get(name)
            if rule is None or field is None:
                continue
            incoming = ret.get(name)
            if not isinstance(incoming, str) or not incoming:
                continue
            source = getattr(field, "source", None) or name
            if source == "*":
                continue
            original = getattr(instance, source, None)
            if not isinstance(original, str) or not original or incoming == original:
                continue
            if incoming == apply_mask(original, rule):
                ret.pop(name, None)
                logger.warning("drop masked write-back value. model:%s field:%s", model._meta.label_lower, name)


class TabsColumn(object):
    def __init__(self, label: str, fields: List[str]):
        self.label = label
        self.fields = fields

    def __str__(self):
        return {"label": self.label, "fields": self.fields}
