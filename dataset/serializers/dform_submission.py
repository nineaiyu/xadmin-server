#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单（提交侧）序列化器：填报 / 提交详情 / 导出 / 表单数据（管理端）。

自 ``dform.py`` 拆出（表单定义侧 vs 提交侧分文件，控制单文件体量）；
``dform.py`` 底部对本次迁出的名字保持历史导入路径再导出。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import BasePrimaryKeyRelatedField, LabeledChoiceField
from common.core.serializers import BaseModelSerializer
from dataset.models.dform import DynamicForm, DynamicFormSubmission
from dataset.utils.dform import (
    trim_stale_schema_keys,
    validate_draft_data,
    validate_submission_data,
)
from dataset.utils.dform_filter import build_filter_data
from dataset.utils.dform_history import (
    key_of,
    merged_fields_of_forms,
    schema_for_version,
    snapshot_resolvable,
    submission_schema,
)

SENSITIVE_MASK = "******"


def export_dynamic_fields(queryset: Any) -> list[tuple[str, str, bool]]:
    """导出动态列：按行集合涉及的表单合并字段（key 去重、保留出现顺序）。

    「我的填报」与「表单数据」（管理端）两个导出口径共用：列集合跟随过滤后的行
    集合，跨表单导出时按表单出现顺序合并。**含历史字段**——当前 schema 已删除、
    但按版本快照可解析的字段一并出列并标注历史（改版后旧值不再随列消失）。
    """
    form_ids = list(queryset.values_list("form_id", flat=True).distinct())
    if not form_ids:
        return []
    forms = DynamicForm.objects.filter(pk__in=form_ids, is_template=False)
    return [
        (key_of(item), str(item.get("label") or key_of(item)), bool(item.get("sensitive")))
        for item in merged_fields_of_forms(forms)
    ]


def form_schema_of(obj: Any) -> list[Any]:
    """表单 schema 快照（详情展示口径：字段 label 与复杂控件按**提交时版本**渲染）。

    - 提交版本命中 `schema_history` 快照 → 用快照（改版后回看历史提交，字段标签与
      控件形态与提交时一致）；当前 schema 已删除的字段标注历史；
    - 快照缺失（版本超出保留窗口）→ 当前 schema 为底 + `data` 中无法识别的键兜底
      （值确定可见，不回退为「看不见」）。
    """
    return submission_schema(obj)


def approval_trail_of(obj: Any, context: Any) -> list[Any]:
    """审批轨迹：实例任务的展示口径（状态/审批人/意见/时间/加签/委托来源）。"""
    if not obj.instance_id:
        return []
    from approval.contracts import serialize_instance_tasks

    tasks = obj.instance.tasks.all()
    payload: list[Any] = serialize_instance_tasks(tasks, context)
    return payload


class FormPkField(serializers.PrimaryKeyRelatedField):
    """表单外键取值域不做行级数据权限过滤（定义类资源）。"""

    def get_queryset(self) -> Any:
        return DynamicForm.objects.all()


class MySubmissionListSerializer(BaseModelSerializer):
    """「我的填报」列表序列化器：固定列 + data 摘要（读写序列化器分离）。

    列表契约只承载列表语义：不做写校验、不含 form_schema / approval_trail——
    逐行展开 schema 快照与 ``approval_trail_of``（每行触发 ``instance.tasks.all()``
    且无 prefetch）是「我的填报」列表的主开销，而列表页不需要这两个详情口径字段。
    详情（抽屉）经 retrieve 单条取全量（SubmissionDetail 自行拉取），
    与「表单数据」页的 FormDataListSerializer 同口径。
    """

    ignore_field_permission = True
    form_name = serializers.CharField(source="form.name", read_only=True)
    status = LabeledChoiceField(choices=DynamicFormSubmission.Status.choices, required=False, read_only=True)

    class Meta:
        model = DynamicFormSubmission
        fields = [
            "pk",
            "form",
            "form_name",
            "schema_version",
            "data",
            "status",
            "creator",
            "created_time",
            "updated_time",
        ]
        read_only_fields = fields
        table_fields = ["form_name", "status", "creator", "created_time"]


class DynamicFormSubmissionSerializer(BaseModelSerializer):
    ignore_field_permission = True
    form_name = serializers.CharField(source="form.name", read_only=True)
    # 覆盖 BaseModelSerializer 默认的 BasePrimaryKeyRelatedField（其取值域
    # 会做数据权限过滤，普通用户无授权即"对象不存在"）
    form = FormPkField(queryset=DynamicForm.objects.all())
    # 状态由审批结果驱动（绑定流程时随实例终态回写），空 = 无需审批已生效
    status = LabeledChoiceField(choices=DynamicFormSubmission.Status.choices, required=False, read_only=True)
    # 表单 schema 快照：详情页渲染字段 label / 复杂控件展示（填报页无需表单设计器权限）
    form_schema = serializers.SerializerMethodField(label=_("Form schema"))
    # 审批轨迹：绑定流程的提交按实例任务读（状态口径与流程审批中心同源）
    approval_trail = serializers.SerializerMethodField(label=_("Approval trail"))

    class Meta:
        model = DynamicFormSubmission
        fields = [
            "pk",
            "form",
            "form_name",
            "form_schema",
            "schema_version",
            "data",
            "status",
            "instance",
            "approval_trail",
            "creator",
            "created_time",
            "updated_time",
        ]
        read_only_fields = ["pk", "schema_version", "creator", "created_time", "updated_time", "instance"]
        table_fields = ["form_name", "status", "creator", "created_time"]

    def get_form_schema(self, obj: Any) -> list[Any]:
        return form_schema_of(obj)

    def get_approval_trail(self, obj: Any) -> list[Any]:
        return approval_trail_of(obj, self.context)

    def validate(self, attrs: Any) -> Any:
        form = attrs.get("form") or getattr(self.instance, "form", None)
        if form is None:
            raise serializers.ValidationError(_("Dynamic form is required"))
        if form.is_template:
            raise serializers.ValidationError(_("A form template cannot accept submissions"))
        # 新建提交要求表单启用；已有 DRAFT/REJECTED 提交允许在表单停用后继续编辑/重提
        if not form.is_active and self.instance is None:
            raise serializers.ValidationError(_("This form is no longer accepting submissions"))
        # 编辑历史提交（未换表单）：按提交时的 schema 快照校验与物化。
        # 前端按快照渲染字段（我的填报编辑、历史详情），后端若按当前 schema 校验，
        # 改版后删字段会被判「未知键」、新增必填会被判「必填缺失」——两侧口径必须同源。
        schema = form.schema
        schema_version = form.schema_version or 1
        if self.instance is not None and form.pk == self.instance.form_id:
            # 用纯快照（不并入 data 中的未知键）：编辑时未知键仍需被裁剪，
            # 只有快照里声明过的字段才参与校验与物化。
            schema = schema_for_version(form, self.instance.schema_version)
            schema_version = self.instance.schema_version or schema_version
        data = attrs.get("data")
        if data is None and self.instance:
            data = self.instance.data
        elif (
            data is not None
            and self.instance is not None
            and getattr(self, "partial", False)
            and isinstance(data, dict)
        ):
            # PATCH 局部更新：data 先与库内数据合并再整份校验——只校验提交子集会把
            # 未提交的必填字段判成缺失（必填误报）。PUT（非 partial）维持整份替换
            # 语义（省略键 = 删除该键）。
            # 合并底数先按生效 schema 裁剪历史键：schema 演进后旧提交
            # 的已删字段键无法经表单清理，合并不裁剪会随载荷重新入库并被拒绝
            data = {
                **trim_stale_schema_keys(schema, self.instance.data or {}),
                **data,
            }
        # 草稿（DRAFT）：轻校验（结构/体积），必填与取值在提交时按完整规则校验
        if self.context.get("draft"):
            attrs["data"] = validate_draft_data(data)
        else:
            # 提交侧校验：与 schema 定义同源（未知键/required/选项/边界；字典字段读字典值；
            # 联动规则参与：隐藏字段跳过校验且不落库、动态必填覆盖字段定义）
            attrs["data"] = validate_submission_data(
                schema,
                data,
                user=getattr(self.request, "user", None),
                previous=getattr(self.instance, "data", None),
            )
        # 记录保存时的表单版本（审计与展示；校验始终按提交当时的 schema）
        attrs["schema_version"] = schema_version
        # 物化筛选列：勾选「可筛选」的字段取值（列表筛选走 JSON 包含查询，可命中 GIN）
        attrs["filter_data"] = build_filter_data(schema, attrs["data"])
        return attrs


class SubmissionDataField(serializers.Field):
    """动态表单提交的「数据列」字段：从 instance.data 按 key 取值（导出展示口径）。

    导出列集合由视图在导出前按涉及表单的 schema 注入（`dynamic_fields` 上下文），
    未在 schema 声明的历史 data 键不导出（表单已删除字段的旧值不再出现在表头）。
    """

    def __init__(self, data_key: Any, sensitive: bool = False, **kwargs: Any) -> None:
        self.data_key = data_key
        self.sensitive = sensitive
        self.mask_sensitive = False
        self.row_fail_closed = False
        super().__init__(**kwargs)

    def get_attribute(self, instance: Any) -> Any:
        # 行级快照可解析性（get_attribute 与 to_representation 按行成对调用）：
        # 版本快照超出保留窗口的提交按 fail-closed 掩码——列敏感性按当前 schema
        # 解析，无法代表提交当时的敏感性，宁可掩码不可明文
        self.row_fail_closed = not snapshot_resolvable(instance)
        return (instance.data or {}).get(self.data_key)

    def to_representation(self, value: Any) -> Any:
        if self.mask_sensitive and (self.sensitive or self.row_fail_closed):
            return SENSITIVE_MASK
        return value


class SubmissionExportSerializer(BaseModelSerializer):
    """动态表单提交导出序列化器：固定列 + 按表单 schema 展开的动态数据列。

    复用导出框架（export-data / 渲染器按 `Meta.model` 定文件名、按 fields 出列）；
    动态字段在 `__init__` 末尾注入（绕开字段权限裁剪：导出列由 schema 决定）。
    「我的填报」与「表单数据」（管理端）两个导出口径共用。
    """

    form_name = serializers.SerializerMethodField(label=_("Form"))
    creator_name = serializers.SerializerMethodField(label=_("Creator"))

    class Meta:
        model = DynamicFormSubmission
        fields = ["pk", "form_name", "creator_name", "created_time"]
        table_fields = ["form_name", "creator_name", "created_time"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        mask_sensitive = self.context.get("mask_sensitive_fields", not getattr(user, "is_superuser", False))
        for key, label, sensitive in self.context.get("dynamic_fields") or []:
            # required=False：导出列标题不带 *（required 标记只对导入模板有意义）
            field = SubmissionDataField(data_key=key, label=label, sensitive=sensitive, required=False)
            field.mask_sensitive = mask_sensitive
            # 手动绑定 field_name：渲染器按 field.field_name 取值与出列名
            field.field_name = key
            self.fields[key] = field

    def get_form_name(self, obj: Any) -> Any:
        return obj.form.name if obj.form_id else ""

    def get_creator_name(self, obj: Any) -> Any:
        return getattr(obj.creator, "username", "") or ""


class FormDataListSerializer(BaseModelSerializer):
    """表单数据（管理端）列表序列化器：固定列 + data 全量（前端按表单 schema 渲染动态列）。

    与「我的填报」的读写序列化器分离（列表契约只承载列表语义）：
    不做写校验、不含 form_schema / approval_trail（详情口径，避免列表逐行展开
    schema 与流程任务造成载荷膨胀）；提交人按 username 回显（非超管同样稳定）。
    """

    ignore_field_permission = True
    form_name = serializers.CharField(source="form.name", read_only=True, label=_("Form"))
    status = LabeledChoiceField(choices=DynamicFormSubmission.Status.choices, required=False, read_only=True)
    creator = BasePrimaryKeyRelatedField(
        attrs=["username"],
        read_only=True,
        ignore_field_permission=True,
        format="{username}",
        label=_("Creator"),
    )

    class Meta:
        model = DynamicFormSubmission
        fields = ["pk", "form_name", "schema_version", "data", "status", "creator", "created_time", "updated_time"]
        read_only_fields = fields
        table_fields = ["form_name", "status", "creator", "created_time"]

    def to_representation(self, instance: Any) -> Any:
        """``?data_fields=key1,key2`` 收缩行内 data 载荷（大 schema 列表页整包回传的主开销）。

        收缩只影响展示载荷（缺 key 视为空值），详情与导出不受影响；key 集合由视图
        校验（合法字段 key 形态）后放入 context，缺省 = 全量（存量行为零变化）。
        """
        data = super().to_representation(instance)
        allowed = self.context.get("data_fields")
        if allowed and isinstance(data.get("data"), dict):
            data["data"] = {key: value for key, value in data["data"].items() if key in allowed}
        # 敏感字段掩码：schema 级 sensitive 标记；超管可见，其余角色一律掩码。
        # 版本快照超出保留窗口（无法确认提交当时敏感性）的提交 fail-closed：
        # 全部动态键掩码，历史敏感键不因版本滚出 schema_history 而明文
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not getattr(user, "is_superuser", False) and isinstance(data.get("data"), dict):
            if not snapshot_resolvable(instance):
                data["data"] = {key: SENSITIVE_MASK for key in data["data"]}
            else:
                sensitive = {str(item.get("key")) for item in form_schema_of(instance) if item.get("sensitive")}
                if sensitive:
                    data["data"] = {
                        key: SENSITIVE_MASK if key in sensitive else value for key, value in data["data"].items()
                    }
        return data


class FormDataDetailSerializer(FormDataListSerializer):
    """表单数据（管理端）详情：列表口径 + schema 快照 / 审批轨迹 / 流程实例号。"""

    form_schema = serializers.SerializerMethodField(label=_("Form schema"))
    approval_trail = serializers.SerializerMethodField(label=_("Approval trail"))
    instance = serializers.SerializerMethodField(label=_("Approval instance"))

    class Meta(FormDataListSerializer.Meta):
        fields = [*FormDataListSerializer.Meta.fields, "form_schema", "approval_trail", "instance"]
        read_only_fields = fields

    def get_form_schema(self, obj: Any) -> list[Any]:
        return form_schema_of(obj)

    def get_approval_trail(self, obj: Any) -> list[Any]:
        return approval_trail_of(obj, self.context)

    def get_instance(self, obj: Any) -> Any:
        return str(obj.instance_id) if obj.instance_id else None
