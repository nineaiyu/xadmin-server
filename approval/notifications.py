#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""approval 域用户消息：流程审批通知 + 审批中心通知（自 system 寄居归位）。

- ApprovalFlowMessage：全量审批流引擎事件面（submitted / approved、rejected /
  remind / added / timeout / transferred / sign_removed / returned / cancelled 等）；
- ApprovalRequestMessage：审批中心通知（提交发审批人 / 通过、驳回发申请人 / 超时提醒）。

message_type（=类名）与 category 串不变，既有订阅行与模板零迁移。
"""

from typing import Any

from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from common.utils.timezone import local_now_display
from notifications.services import UserMessage, register_message


@register_message
class ApprovalFlowMessage(UserMessage):
    """流程审批通知（全量审批流引擎）。"""

    category = "Audit"
    category_label = _("Audit")
    message_type_label = _("Approval flow notice")

    EVENT_TITLES = {
        "submitted": _("New approval application"),
        "approved": _("Approval application approved"),
        "rejected": _("Approval application rejected"),
        "remind": _("Approval task pending reminder"),
        "urge": _("Approval request urged by the applicant"),
        "added": _("Added as approval approver"),
        "transferred": _("Approval task transferred to you"),
        # timeout = 超时自动动作（系统代处理发原处理人）/ sign_removed = 减签 /
        # returned = 退回重审（发新候选与申请人）/ cc、mentioned = 协作事件
        "timeout": _("Approval task auto-processed on timeout"),
        "sign_removed": _("Your added approval task has been removed"),
        "returned": _("Approval returned for re-processing"),
        "cancelled": _("Approval application cancelled"),
        # auto_approved = 节点无候选自动通过（治理告警，知会超管）
        "auto_approved": _("Approval node auto-approved due to no available approver"),
        "cc": _("Approval application copied to you"),
        "mentioned": _("You were mentioned in the approval discussion"),
    }

    def __init__(self, user: Any, event: str, instance: Any, extra: str = "", node_name: str = "") -> None:
        self.event = event
        self.instance = instance
        self.extra = extra
        # 显式节点名（事件发生在节点尚未成为 current_node 时由调用方给出）
        self.node_name = node_name
        super().__init__(user)

    @classmethod
    def template_variables(cls) -> tuple[Any, ...]:
        """模板可用业务变量（与 get_template_vars 同源，两者漂移由守护测试拦下）。"""
        return (
            "title",
            "flow_name",
            "node_name",
            "instance_no",
            "reason",
            "extra",
            "name",
            "event",
            "time",
        )

    def get_template_vars(self) -> dict[str, Any]:
        instance = self.instance
        return {
            "title": instance.title or "-",
            "flow_name": instance.flow_name or "-",
            "node_name": self.node_name or getattr(instance.current_node, "name", "") or self.extra or "-",
            "instance_no": str(instance.pk or "")[:8].upper(),
            "reason": instance.reason or "",
            "extra": self.extra or "",
            "name": self.user_display,
            "event": self.event,
            # 渲染时刻：默认渠道模板（notify/msg_approval_flow.html）同样引用该变量，
            # 不登记会导致自定义模板写 {{ time }} 被保存校验拒绝
            "time": local_now_display(),
        }

    def get_html_msg(self) -> dict[str, Any]:
        subject = self.EVENT_TITLES.get(self.event, self.EVENT_TITLES["submitted"])
        # 业务变量（get_template_vars，与模板覆盖同源）+ 渲染补充字段
        context = dict(self.get_template_vars())
        context["subject"] = subject
        message = render_to_string("notify/msg_approval_flow.html", context)
        return {"subject": subject, "message": message}

    @classmethod
    def gen_test_msg(cls) -> Any:
        from approval.models import ApprovalFlow, ApprovalInstance
        from identity.services import UserInfo

        user = UserInfo.objects.first()
        instance = ApprovalInstance(flow=ApprovalFlow(name="Test", code="test"), flow_name="Test", title="Test")
        return cls(user, "submitted", instance)


@register_message
class ApprovalRequestMessage(UserMessage):
    """审批中心通知：提交（发审批人）/ 通过、驳回（发申请人）三种文案。"""

    category = "Audit"
    category_label = _("Audit")
    message_type_label = _("Approval request notice")

    EVENT_TITLES = {
        "submitted": _("New approval request"),
        "approved": _("Approval request approved"),
        "rejected": _("Approval request rejected"),
        # 超时未处理提醒（每日任务补发一次，见 system.utils.approval.remind_pending_approvals）
        "remind": _("Approval request pending reminder"),
    }

    def __init__(self, user: Any, event: str, approval: Any) -> None:
        self.event = event
        self.approval = approval
        super().__init__(user)

    def get_html_msg(self) -> dict[str, Any]:
        approval = self.approval
        subject = self.EVENT_TITLES.get(self.event, self.EVENT_TITLES["submitted"])
        context = dict(
            subject=subject,
            name=self.user_display,
            event=self.event,
            module=approval.module or "-",
            method=approval.method or "-",
            path=approval.path or "-",
            approval_no=str(approval.pk)[:8].upper(),
            reason=approval.reason or "",
            time=local_now_display(),
        )
        message = render_to_string("notify/msg_approval.html", context)
        return {"subject": subject, "message": message}

    @classmethod
    def gen_test_msg(cls) -> Any:
        from approval.models import ApprovalRequest
        from identity.models import UserInfo

        user = UserInfo.objects.first()
        approval = ApprovalRequest(module="User", method="DELETE", path="/api/system/user/1", creator=user)
        return cls(user, "submitted", approval)
