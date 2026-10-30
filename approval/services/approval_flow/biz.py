#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""审批流实例的关联业务对象摘要：biz_type 白名单渲染器。

实例详情页展示「关联业务对象当前状态」卡片：审批人不必跳到业务页就能确认
申请对象是否仍然存在、当前业务状态是什么（驳回 / 重提后同步刷新）。

- 白名单渲染（dform_submission / leave / demo_book，即已接入的三类）；
- 业务行不可达（已删除 / 模块裁剪）返回 missing=True，前端按「对象已不可达」降级；
- 只读、异常隔离（渲染失败不影响实例详情本身）。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _

from common.core.registry import Registry
from common.utils import get_logger

logger = get_logger(__name__)

MAX_FIELDS = 8


def _text(value: Any) -> str:
    """DictChoiceField 形态（{value,label}）或标量 → 展示文本。"""
    if isinstance(value, dict):
        return str(value.get("label") or value.get("value") or "")
    return "" if value is None else str(value)


def _missing(biz_type: str, label: Any = None) -> dict[str, Any]:
    return {
        "type": biz_type,
        "label": str(label) if label else biz_type,
        "title": "",
        "status": "",
        "fields": [],
        "missing": True,
    }


def _render_dform_submission(instance: Any) -> dict[str, Any]:
    from dataset.contracts import DynamicFormSubmission

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


def _render_leave(instance: Any) -> dict[str, Any]:
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


def _render_demo_book(instance: Any) -> dict[str, Any]:
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


#: biz_type → 摘要渲染器注册表（内置三类 + 运行期扩展，键冲突 fail-fast、可重置）
RENDERERS = Registry(
    "approval_biz_renderers",
    {
        "dform_submission": _render_dform_submission,
        "leave": _render_leave,
        "demo_book": _render_demo_book,
    },
)


def register_biz_renderer(biz_type: str) -> Any:
    """装饰器：登记 biz_type 的关联对象摘要渲染器（二开接入新业务类型的扩展点）。

    新业务类型此前需要改本模块源码；现在业务 app 在自己的模块里::

        from approval.services.approval_flow.biz import register_biz_renderer

        @register_biz_renderer("my_biz")
        def _render_my_biz(instance): ...

    渲染协议与内置一致：返回摘要 dict；抛异常按「对象不可达」降级，不影响实例详情。
    """

    def wrap(func: Any) -> Any:
        RENDERERS.register(biz_type, func)
        return func

    return wrap


def biz_summary(instance: Any) -> dict[str, Any] | None:
    """返回业务对象摘要；无关联时 None（前端不渲染卡片）。"""
    biz_type = str(getattr(instance, "biz_type", "") or "")
    biz_id = str(getattr(instance, "biz_id", "") or "")
    if not biz_type or not biz_id:
        return None
    renderer = RENDERERS.get(biz_type)
    if renderer is None:
        return _missing(biz_type)
    try:
        # Registry.get 返回 Any；渲染协议固定返回摘要 dict（见 register_biz_renderer 注释）
        summary: dict[str, Any] | None = renderer(instance)
        return summary
    except Exception:  # noqa: BLE001 渲染失败按不可达降级，不影响实例详情
        logger.warning("render biz summary failed. type:%s id:%s", biz_type, biz_id, exc_info=True)
        return _missing(biz_type)
