#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""审批流实例的关联业务对象摘要：biz_type 白名单渲染器。

实例详情页展示「关联业务对象当前状态」卡片：审批人不必跳到业务页就能确认
申请对象是否仍然存在、当前业务状态是什么（驳回 / 重提后同步刷新）。

- 白名单渲染（dform_submission / leave / demo_book，即 ADR-044 已接入的三类）；
- 业务行不可达（已删除 / 模块裁剪）返回 missing=True，前端按「对象已不可达」降级；
- 只读、异常隔离（渲染失败不影响实例详情本身）。
"""

from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

MAX_FIELDS = 8


def _text(value) -> str:
    """DictChoiceField 形态（{value,label}）或标量 → 展示文本。"""
    if isinstance(value, dict):
        return str(value.get("label") or value.get("value") or "")
    return "" if value is None else str(value)


def _missing(biz_type: str, label=None) -> dict:
    return {
        "type": biz_type,
        "label": str(label) if label else biz_type,
        "title": "",
        "status": "",
        "fields": [],
        "missing": True,
    }


def _render_dform_submission(instance) -> dict:
    from dataset.services import DynamicFormSubmission

    row = DynamicFormSubmission.objects.select_related("form", "creator").filter(pk=instance.biz_id).first()
    if row is None:
        return _missing("dform_submission", _("Dynamic form"))
    return {
        "type": "dform_submission",
        "label": str(_("Dynamic form")),
        "title": getattr(row.form, "name", "") or "",
        "status": _text(row.get_status_display() if row.status else ""),
        "fields": [
            {"label": str(_("Submitted by")), "value": getattr(row.creator, "username", "") or ""},
        ],
        "missing": False,
    }


def _render_leave(instance) -> dict:
    from approval.models.leave import Leave

    row = Leave.objects.filter(pk=instance.biz_id).first()
    if row is None:
        return _missing("leave", _("Leave request"))
    return {
        "type": "leave",
        "label": str(_("Leave request")),
        "title": row.approval_title,
        "status": row.get_status_display(),
        "fields": [
            {"label": str(_("Reason")), "value": (row.reason or "")[:80]},
        ],
        "missing": False,
    }


def _render_demo_book(instance) -> dict:
    from django.apps import apps

    try:
        model = apps.get_model("demo", "Book")
    except LookupError:
        # 模块裁剪（demo 未注册）时降级展示，不影响实例详情
        return _missing("demo_book", _("Book"))
    row = model.objects.filter(pk=instance.biz_id).first()
    if row is None:
        return _missing("demo_book", _("Book"))
    return {
        "type": "demo_book",
        "label": str(_("Book")),
        "title": str(row)[:120],
        "status": _text(getattr(row, "get_status_display", lambda: "")()),
        "fields": [],
        "missing": False,
    }


RENDERERS = {
    "dform_submission": _render_dform_submission,
    "leave": _render_leave,
    "demo_book": _render_demo_book,
}


def biz_summary(instance) -> dict | None:
    """返回业务对象摘要；无关联时 None（前端不渲染卡片）。"""
    biz_type = str(getattr(instance, "biz_type", "") or "")
    biz_id = str(getattr(instance, "biz_id", "") or "")
    if not biz_type or not biz_id:
        return None
    renderer = RENDERERS.get(biz_type)
    if renderer is None:
        return _missing(biz_type)
    try:
        return renderer(instance)
    except Exception:  # noqa: BLE001 渲染失败按不可达降级，不影响实例详情
        logger.warning("render biz summary failed. type:%s id:%s", biz_type, biz_id, exc_info=True)
        return _missing(biz_type)
