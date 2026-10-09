#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializers
# author : ly_13
# date : 12/21/2023
import asyncio
import os
from collections.abc import Iterator
from inspect import isfunction
from typing import Any

from django.db.models import QuerySet
from django.db.models.fields import NOT_PROVIDED
from rest_framework.fields import empty
from rest_framework.request import Request
from rest_framework.serializers import ModelSerializer

from common.contracts import apply_grant_fields
from common.core.fields import BasePrimaryKeyRelatedField as BasePrimaryKeyRelatedField
from common.core.fields import LabeledChoiceField
from common.core.mask import apply_mask as apply_mask
from common.core.mask import apply_output_mask, mask_exempt
from common.core.mask import get_mask_rules as get_mask_rules
from common.local import get_current_request
from common.settings_contract import kernel_required_setting
from common.utils import get_logger

logger = get_logger(__name__)


def _running_in_event_loop() -> bool:
    """当前线程是否存在运行中的事件循环（与 Django async_unsafe 的判定同款）。

    事件循环线程内不能做同步 DB 访问；asgiref 线程敏感执行器（同步视图的实际执行处、
    管理命令、WSGI 线程）没有运行中的事件循环，不受影响。
    """
    if os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE"):
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class BaseModelSerializer(ModelSerializer):
    serializer_related_field = BasePrimaryKeyRelatedField
    serializer_choice_field = LabeledChoiceField
    ignore_field_permission = False  # 忽略字段权限
    # 行级归属守卫开关：声明为 True 的序列化器在输出中注入 is_owner
    # （当前访问者是 creator 或超管），前端行内按钮按其显隐；与各域写守卫
    # （非 creator 修改返回 1003）同一口径。按域显式开启——守卫落在哪个域，
    # 标记就下发到哪个域，避免无守卫语义的接口让前端误藏按钮。
    row_owner_guard = False

    class Meta:
        model: Any = None
        table_fields: list[Any] = []  # 用于控制前端table的字段展示
        tabs: list[Any] = []

    def get_field_names(self, declared_fields: Any, info: Any) -> Any:
        """将默认的id字段 转换为 pk，并并入 Meta.tabs 声明的分组字段。

        tabs 字段在实例级合并，而不是改写类级 ``Meta.fields``：``Meta`` 是类属性，
        就地追加会随实例化次数不断膨胀，且只对"下一次"实例化生效（首次实例化丢字段）。
        """
        fields = super().get_field_names(declared_fields, info)
        if "id" in fields:
            fields = ["pk"] + [f for f in fields if f != "id"]
        meta = getattr(self, "Meta", None)
        tabs = getattr(meta, "tabs", None)
        if tabs and getattr(meta, "fields", None) != "__all__":
            for name in self.get_fields_from_tabs(tabs):
                if name not in fields:
                    fields.append(name)
        return fields

    def get_value(self, dictionary: Any) -> Any:
        # We override the default field access in order to support
        # nested HTML forms.
        # 下面两行注释是因为已经在前面处理过form-data，这里无需再次处理
        # if html.is_html_input(dictionary):
        #     return html.parse_html_dict(dictionary, prefix=self.field_name) or empty
        return dictionary.get(self.field_name, empty)

    def get_allow_fields(self, fields: Any, ignore_field_permission: Any) -> Any:
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
            return self._converge_grant_fields(set(fields) & _fields)

        allow_fields: list[Any] | set[Any] = []
        # 取角色-菜单维度的字段白名单（request.fields[模型]）；**未配置 = 零字段**
        # （fail-closed，接口输出空对象，而非"未配置即全字段"）——漏配字段权限会被
        # 静默裁空，故审计面把「角色有权限点无字段权限」列为告警
        # （system/services/permission_sync/audit.py::audit_field_permissions）。
        if self.request and kernel_required_setting("PERMISSION_FIELD_ENABLED") and not self.ignore_field_permission:
            if hasattr(self.request, "user") and self.request.user and self.request.user.is_superuser:
                allow_fields = _fields
            elif hasattr(self.request, "fields"):
                if self.request.fields and isinstance(self.request.fields, dict):
                    allow_fields = self.request.fields.get(self.Meta.model._meta.label_lower, [])
        else:
            allow_fields = _fields

        return self._converge_grant_fields(set(fields) & _fields & set(allow_fields))

    def _converge_grant_fields(self, allowed: Any) -> Any:
        """应用字段级授权收敛。

        约束挂在凭证（应用）维度：**穿透字段权限豁免**（超管 / 白名单 URL /
        字段权限开关关闭时同样收敛），未配置应用授权时原样返回。
        """
        if not self.request:
            return allowed
        return apply_grant_fields(self.request, self.Meta.model._meta.label_lower, allowed)

    def __init__(
        self,
        instance: Any = None,
        data: Any = empty,
        fields: Any = None,
        ignore_field_permission: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        :param instance:
        :param data:
        :param request: Request 对象
        :param fields: 序列化展示的字段， 默认定义的全部字段
        :param ignore_field_permission: 忽略字段权限控制
        """
        super().__init__(instance, data, **kwargs)
        self.request: Request = get_current_request()
        if self.request is None:
            return
        # 事件循环线程内不做字段收敛：ASGI 下 URLconf 首次加载发生在事件循环线程，
        # 而声明式嵌套序列化器（字段在类体实例化）与 @extend_schema 响应里的 schema
        # 序列化器会在**导入期**实例化——此处继续绑定字段会触发字典字段解析（事件循环
        # 线程内的同步 DB 读取被 Django 拦截并降级），且导入期实例的字段收敛没有请求
        # 语义（request 恰为"正在加载 URLconf 的那个请求"）。请求线程内的实例会按当次
        # 请求 deepcopy 重做绑定与收敛，输出口径不受影响。
        if _running_in_event_loop():
            return
        # 记录显式豁免参数，供输出侧（to_representation 脱敏）与 get_allow_fields 同口径判断
        self.ignore_field_permission = self.ignore_field_permission or ignore_field_permission
        allowed = self.get_allow_fields(fields, ignore_field_permission)
        for field_name in set(self.fields) - allowed:
            self.fields.pop(field_name)

    @staticmethod
    def get_fields_from_tabs(tabs: list[Any]) -> list[str]:
        seen = set()
        result = []
        for tab in tabs:
            for field in tab.fields:
                if field not in seen:
                    seen.add(field)
                    result.append(field)
        return result

    def get_page_instances(self, default: Any = None) -> Any:
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

    def build_standard_field(self, field_name: Any, model_field: Any) -> Any:
        field_class, field_kwargs = super().build_standard_field(field_name, model_field)
        default = getattr(model_field, "default", NOT_PROVIDED)
        if default != NOT_PROVIDED:
            # 将model中的默认值同步到序列化中
            if isfunction(default):
                default = default()
            field_kwargs.setdefault("default", default)
        return field_class, field_kwargs

    def _iter_upload_file_fields(self, validated_data: Any) -> Iterator[tuple[str, Any, bool]]:
        """产出 ``(字段名, 值, 是否多值)``：仅限关联 ``file.UploadFile`` 的字段。

        create / update 共用同一份关联文件识别逻辑，避免两处判定条件各自漂移。
        """
        for field in self.Meta.model._meta.get_fields():
            if not (field.is_relation and field.related_model._meta.label == "file.UploadFile"):
                continue
            if field.name not in validated_data:
                continue
            file_data = validated_data[field.name]
            yield field.name, file_data, isinstance(file_data, (list, QuerySet))

    @staticmethod
    def _mark_upload_files_used(file_objs: Any) -> None:
        """新关联的文件由临时态转正式态（未被引用的临时文件会被清理任务回收）。"""
        for file_obj in file_objs:
            file_obj.is_tmp = False
            file_obj.save(update_fields=["is_tmp"])

    def create(self, validated_data: Any) -> Any:
        n_file_objs: list[Any] = []
        for _name, file_data, many in self._iter_upload_file_fields(validated_data):
            if many:
                n_file_objs.extend(file_data or [])
            elif file_data is not None:
                # 可空外键允许显式传 null（清空附件），临时态标记只针对真实文件
                n_file_objs.append(file_data)

        result = super().create(validated_data)
        self._mark_upload_files_used(n_file_objs)
        return result

    def update(self, instance: Any, validated_data: Any) -> Any:
        n_file_objs: list[Any] = []
        d_file_objs: list[Any] = []
        for name, file_data, many in self._iter_upload_file_fields(validated_data):
            if many:
                # 关联实例各取一次，避免原来 set(...all()) 两次触发同一查询
                # 显式传 null / 空数组 = 清空附件
                old_file_objs = set(getattr(instance, name).all())
                new_file_objs = set(file_data or [])
                d_file_objs.extend(old_file_objs - new_file_objs)
                n_file_objs.extend(new_file_objs - old_file_objs)
            else:
                o_file_obj = getattr(instance, name)
                n_file_obj = file_data
                # 可空外键两侧都可能为 None（清空附件 / 原本就为空），不能直接取 pk
                if (o_file_obj.pk if o_file_obj else None) != (n_file_obj.pk if n_file_obj else None):
                    if o_file_obj:
                        d_file_objs.append(o_file_obj)
                    if n_file_obj:
                        n_file_objs.append(n_file_obj)

        result = super().update(instance, validated_data)
        for d_file in d_file_objs:
            d_file.delete()
        self._mark_upload_files_used(n_file_objs)
        return result

    def _mask_exempt(self, request: Any, user: Any, model: Any = None) -> Any:
        """脱敏豁免判定：委托统一入口（``common.core.mask.mask_exempt``）。

        保留此方法名：既有测试/子类按序列化器口径调用它。
        """
        return mask_exempt(request, user, model, ignore_field_permission=self.ignore_field_permission)

    def _inject_row_ownership(self, ret: Any, instance: Any, user: Any) -> None:
        """行级归属标记（is_owner）注入：仅 ``row_owner_guard`` 开启的域生效。"""
        if not self.row_owner_guard or not ret or instance is None:
            return
        creator_id = getattr(instance, "creator_id", None)
        if creator_id is None:
            return
        ret["is_owner"] = bool(user is not None and (getattr(user, "is_superuser", False) or creator_id == user.pk))

    def to_representation(self, instance: Any) -> Any:
        """字段级数据脱敏钩子：列表/详情/导出同一条输出链路统一掩码。

        掩码与豁免口径集中在 ``common.core.mask.apply_output_mask``（与关联字段、
        全局搜索共用同一实现）；只脱敏输出，写入不受影响（写入侧的掩码回写守护
        在 to_internal_value）。
        """
        ret = super().to_representation(instance)
        model = getattr(getattr(self, "Meta", None), "model", None)
        request = self.request
        if model is None or request is None:
            return ret
        user = getattr(request, "user", None)
        self._inject_row_ownership(ret, instance, user)
        return apply_output_mask(ret, request, user, model, self.ignore_field_permission)

    def to_internal_value(self, data: Any) -> Any:
        """写入侧统一入口：丢弃「掩码回写」的字段值（数据完整性守护）。

        编辑弹窗的初始值来自被掩码的输出，用户未修改该字段时会把 ``138******78``
        原样提交；这里与库内原文比对（等于原文的掩码结果则丢弃），避免原文被掩码串
        覆盖（不可逆）。真正的改动（提交值 != 掩码结果）照常写入。
        """
        ret = super().to_internal_value(data)
        self._drop_masked_writeback(ret)
        return ret

    def _drop_masked_writeback(self, ret: Any) -> None:
        instance = self.instance
        model = getattr(getattr(self, "Meta", None), "model", None)
        if instance is None or not ret or model is None:
            return
        rules = get_mask_rules(model._meta.label_lower)
        if not rules:
            return
        rule_map: dict[str, Any] = {}
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


class TabsColumn:
    def __init__(self, label: str, fields: list[str]) -> None:
        self.label = label
        self.fields = fields

    def __str__(self) -> Any:
        return {"label": self.label, "fields": self.fields}
