#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通知消息模板管理。

注册表以「消息类型」为行（代码注册的消息类），DB 覆盖为可选层：

- ``list``：注册表 + 覆盖状态 + 可用变量（管理页表格数据源）；
- ``preview``：未保存的模板文本按样例变量渲染（保存前可见效果）；
- ``save``：保存覆盖（校验模板语法与变量白名单，保存即失效缓存）；
- ``reset``：删除覆盖行（回到代码默认模板）。
"""

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from notifications.models import MessageTemplate
from notifications.notifications import SYSTEM_MESSAGE_REGISTRY, USER_MESSAGE_REGISTRY, SystemMessage
from notifications.serializers.template import MessageTemplateResetSerializer, MessageTemplateWriteSerializer
from notifications.template_registry import (
    COMMON_VARIABLES,
    invalidate_overrides,
    render_template,
    validate_template,
)

logger = get_logger(__name__)


def _find_message_cls(message_type):
    for info in SYSTEM_MESSAGE_REGISTRY + USER_MESSAGE_REGISTRY:
        if info["message_type"] == message_type:
            return info["cls"], info
    return None, None


def _sample_message(cls):
    """样例消息实例（gen_test_msg 未实现的消息类型返回 None）。"""
    try:
        msg = cls.gen_test_msg()
    except NotImplementedError:
        return None
    except Exception:  # noqa: BLE001 样例构造失败不阻断管理页
        logger.warning("build sample message failed. cls:%s", cls, exc_info=True)
        return None
    return msg


class MessageTemplateViewSet(GenericViewSet):
    """通知消息模板"""

    @extend_schema(request=None, responses=get_default_response_schema())
    def list(self, request, *args, **kwargs):
        """消息类型注册表（含覆盖状态与可用变量）"""
        overrides = {row.message_type: row for row in MessageTemplate.objects.all()}
        items = []
        for info in SYSTEM_MESSAGE_REGISTRY + USER_MESSAGE_REGISTRY:
            cls = info["cls"]
            row = overrides.get(info["message_type"])
            sample = _sample_message(cls)
            default_subject = ""
            if sample is not None:
                try:
                    default_subject = str(sample.get_html_msg().get("subject") or "")
                except Exception:  # noqa: BLE001
                    default_subject = ""
            items.append(
                {
                    "message_type": info["message_type"],
                    "message_type_label": str(info["message_type_label"]),
                    "category": str(info["category"]),
                    "category_label": str(info["category_label"]),
                    "is_system": issubclass(cls, SystemMessage),
                    "default_subject": default_subject,
                    "variables": [*COMMON_VARIABLES, *cls.template_variables()],
                    "has_override": bool(
                        row and ((row.subject_template or "").strip() or (row.body_template or "").strip())
                    ),
                    "override": {
                        "subject_template": row.subject_template if row else "",
                        "body_template": row.body_template if row else "",
                        "is_active": row.is_active if row else True,
                        "remark": row.remark if row else "",
                        "updated_time": row.updated_time if row else None,
                    },
                }
            )
        return ApiResponse(data=items)

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "message_type": build_basic_type(OpenApiTypes.STR),
                    "subject_template": build_basic_type(OpenApiTypes.STR),
                    "body_template": build_basic_type(OpenApiTypes.STR),
                },
                required=["message_type"],
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="preview")
    def preview(self, request, *args, **kwargs):
        """按样例变量渲染模板预览（保存前可见效果）"""
        serializer = MessageTemplateWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        cls, _info = _find_message_cls(data["message_type"])
        if cls is None:
            return ApiResponse(code=1001, detail=_("Unknown message type"))
        sample = _sample_message(cls)
        if sample is None:
            return ApiResponse(code=1001, detail=_("This message type does not support preview"))
        base = sample.get_html_msg()
        context = {
            "subject": str(base.get("subject") or ""),
            "message": str(base.get("message") or ""),
            "message_type": cls.get_message_type(),
        }
        try:
            context.update(sample.get_template_vars() or {})
        except Exception:  # noqa: BLE001 业务变量取值失败不影响默认模板预览
            pass
        # 草稿模板：请求显式给出优先，其次已保存的覆盖行（未保存时两者皆空 → 默认渲染）
        row = MessageTemplate.objects.filter(message_type=data["message_type"]).first()
        subject_template = data.get("subject_template") or (row.subject_template if row else "")
        body_template = data.get("body_template") or (row.body_template if row else "")
        result = {
            "subject": base.get("subject") or "",
            "message": base.get("message") or "",
            "variables": {key: str(value) for key, value in context.items()},
        }
        if str(subject_template or "").strip():
            result["subject"] = render_template(subject_template, context)
        if str(body_template or "").strip():
            result["message"] = render_template(body_template, context)
        return ApiResponse(data=result)

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "message_type": build_basic_type(OpenApiTypes.STR),
                    "subject_template": build_basic_type(OpenApiTypes.STR),
                    "body_template": build_basic_type(OpenApiTypes.STR),
                    "is_active": build_basic_type(OpenApiTypes.BOOL),
                    "remark": build_basic_type(OpenApiTypes.STR),
                },
                required=["message_type"],
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="save")
    def save(self, request, *args, **kwargs):
        """保存模板覆盖（语法与变量白名单校验）"""
        serializer = MessageTemplateWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        cls, _info = _find_message_cls(data["message_type"])
        if cls is None:
            return ApiResponse(code=1001, detail=_("Unknown message type"))
        variables = [*COMMON_VARIABLES, *cls.template_variables()]
        for field, label in (("subject_template", _("Subject")), ("body_template", _("Body"))):
            error = validate_template(data.get(field) or "", variables)
            if error:
                return ApiResponse(code=1001, detail=f"{label}: {error}")
        row, _created = MessageTemplate.objects.update_or_create(
            message_type=data["message_type"],
            defaults={
                "subject_template": data.get("subject_template") or "",
                "body_template": data.get("body_template") or "",
                "is_active": data.get("is_active", True),
                "remark": data.get("remark") or "",
                "modifier": request.user,
            },
        )
        if row.creator_id is None:
            row.creator = request.user
            row.save(update_fields=["creator"])
        invalidate_overrides()
        return ApiResponse(detail=_("Template saved"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={"message_type": build_basic_type(OpenApiTypes.STR)},
                required=["message_type"],
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="reset")
    def reset(self, request, *args, **kwargs):
        """重置回代码默认模板（删除覆盖行）"""
        serializer = MessageTemplateResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        MessageTemplate.objects.filter(message_type=serializer.validated_data["message_type"]).delete()
        invalidate_overrides()
        return ApiResponse(detail=_("Reset to default template"))
