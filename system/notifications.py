import hashlib
import re

from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from common.utils.request import get_request_ip, get_browser
from common.utils.timezone import local_now_display
from notifications.services import (
    BACKEND,
    SystemMessage,
    SystemMsgSubscription,
    UserMessage,
    register_message,
)
from system.services import get_active_superuser_queryset

logger = get_logger(__name__)


@register_message
class DifferentCityLoginMessage(UserMessage):
    category = "AccountSecurity"
    category_label = _("Account Security")
    message_type_label = _("Different city login reminder")

    def __init__(self, user, ip, city):
        self.ip = ip
        self.city = city
        super().__init__(user)

    def get_html_msg(self) -> dict:
        now = local_now_display()
        subject = _("Different city login reminder")
        context = dict(
            subject=subject,
            name=self.user.nickname,
            username=self.user.username,
            ip=self.ip,
            time=now,
            city=self.city,
        )
        message = render_to_string("notify/msg_different_city.html", context)
        return {"subject": subject, "message": message}

    @classmethod
    def gen_test_msg(cls):
        from system.models import UserInfo

        user = UserInfo.objects.first()
        ip = "8.8.8.8"
        city = "洛杉矶"
        return cls(user, ip, city)


@register_message
class AbnormalLoginMessage(UserMessage):
    """新设备/新 IP 登录提醒（异常登录第二维度，与异地城市提醒互补）。"""

    category = "AccountSecurity"
    category_label = _("Account Security")
    message_type_label = _("New device login reminder")

    def __init__(self, user, dimensions, info):
        # dimensions: 新维度清单（如 ["ip", "device"]）
        self.dimensions = dimensions
        self.info = info
        super().__init__(user)

    def get_html_msg(self) -> dict:
        subject = _("New device login reminder")
        dimension_texts = {
            "ip": _("New IP address"),
            "city": _("New city"),
            "device": _("New device (browser/system)"),
        }
        info = self.info or {}
        context = dict(
            subject=subject,
            name=self.user.nickname,
            username=self.user.username,
            # 维度清单在 Python 侧翻译好后传入模板，模板不再做带参数的翻译
            dimensions=[dimension_texts.get(d, d) for d in self.dimensions],
            ip=info.get("ip") or "-",
            city=info.get("city") or "-",
            browser=info.get("browser") or "-",
            system=info.get("system") or "-",
            time=info.get("time") or "-",
        )
        message = render_to_string("notify/msg_abnormal_login.html", context)
        return {"subject": subject, "message": message}

    @classmethod
    def gen_test_msg(cls):
        from system.models import UserInfo

        user = UserInfo.objects.first()
        return cls(
            user,
            ["ip", "device"],
            {"ip": "8.8.8.8", "browser": "Chrome", "system": "macOS", "time": local_now_display()},
        )


@register_message
class ResetPasswordSuccessMsg(UserMessage):
    category = "AccountSecurity"
    category_label = _("Account Security")
    message_type_label = _("Reset password reminder")

    def __init__(self, user, request):
        super().__init__(user)
        self.ip_address = get_request_ip(request)
        self.browser = get_browser(request)

    def get_html_msg(self) -> dict:
        user = self.user

        subject = _("Reset password success")
        context = {
            "name": user.nickname,
            "username": user.username,
            "ip_address": self.ip_address,
            "browser": self.browser,
        }
        message = render_to_string("notify/msg_rest_password_success.html", context)
        return {"subject": subject, "message": message}

    @classmethod
    def gen_test_msg(cls):
        pass


@register_message
class SensitiveOperationMessage(SystemMessage):
    """敏感操作告警（审计）：命中方法/路径清单的操作，经站内信 + 邮件告知全部超管。"""

    category = "Audit"
    category_label = _("Audit")
    message_type_label = _("Sensitive operation alert")

    def __init__(self, operation: dict):
        self.operation = operation

    def get_html_msg(self) -> dict:
        op = self.operation
        subject = _("Sensitive operation alert: {} {}").format(op.get("method"), op.get("path"))
        message = render_to_string(
            "notify/msg_sensitive_operation.html",
            {
                "module": op.get("module") or "-",
                "method": op.get("method") or "-",
                "path": op.get("path") or "-",
                "ipaddress": op.get("ipaddress") or "-",
                "time": op.get("created_time") or "-",
            },
        )
        return {"subject": subject, "message": message}

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info["level"] = "danger"
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        subscription.users.add(*get_active_superuser_queryset())
        subscription.receive_backends = [BACKEND.SITE_MSG, BACKEND.EMAIL]
        subscription.save()

    def publish(self, is_async=False):
        """发布告警；订阅收件人为空时自愈补齐活跃超管。

        存量库可能在超管初始化前就建好订阅（post_migrate 种子时机不保证），
        不自愈会让敏感操作告警永久静默。参照 ServerPerformanceMessage 的做法。
        """
        subscription = SystemMsgSubscription.objects.get(message_type=self.get_message_type())
        if not subscription.users.exists():
            self.post_insert_to_db(subscription)
        super().publish(is_async=is_async)

    @classmethod
    def gen_test_msg(cls):
        return cls(
            {
                "module": _("Operation log"),
                "path": "/api/system/user/1",
                "method": "DELETE",
                "ipaddress": "127.0.0.1",
                "created_time": local_now_display(),
            }
        )


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

    def __init__(self, user, event: str, approval):
        self.event = event
        self.approval = approval
        super().__init__(user)

    def get_html_msg(self) -> dict:
        approval = self.approval
        subject = self.EVENT_TITLES.get(self.event, self.EVENT_TITLES["submitted"])
        context = dict(
            subject=subject,
            name=self.user.nickname,
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
    def gen_test_msg(cls):
        from system.models import UserInfo, ApprovalRequest

        user = UserInfo.objects.first()
        approval = ApprovalRequest(module="User", method="DELETE", path="/api/system/user/1", creator=user)
        return cls(user, "submitted", approval)


SENSITIVE_ALERT_THROTTLE_SECONDS = 60


def maybe_alert_sensitive_operation(info: dict):
    """敏感操作命中判定 + 节流告警（由操作日志中间件在日志落库后调用）。

    方法清单（SysConfig.SENSITIVE_OPERATION_METHODS，默认 ["DELETE"]）与路径正则
    清单（SENSITIVE_OPERATION_PATHS，默认空）AND 组合；同一 方法+路径 60 秒内
    只告警一次。任何异常都不影响请求响应。
    """
    from common.core.config import SysConfig
    from django.core.cache import cache

    methods = SysConfig.SENSITIVE_OPERATION_METHODS
    method = info.get("method")
    if methods and methods != "ALL" and method not in methods:
        return
    paths = SysConfig.SENSITIVE_OPERATION_PATHS
    path = info.get("path") or ""
    if paths:
        try:
            matched = any(re.search(pattern, path) for pattern in paths if pattern)
        except re.error:
            # 管理员配置了非法正则：跳过路径过滤并在本函数内消化告警，
            # 避免把 re.error 抛回中间件造成每个命中请求一条带堆栈的 warning
            logger.warning("sensitive operation alert skipped: invalid path regex %s", paths)
            return
        if not matched:
            return

    digest = hashlib.md5(f"{method}:{path}".encode()).hexdigest()
    if not cache.add(f"sensitive_op_alert_{digest}", 1, SENSITIVE_ALERT_THROTTLE_SECONDS):
        return
    try:
        SensitiveOperationMessage(
            {
                "module": info.get("module"),
                "path": path,
                "method": method,
                "ipaddress": info.get("ipaddress"),
                "created_time": local_now_display(),
            }
        ).publish(is_async=True)
    except Exception:
        logger.warning("send sensitive operation alert failed", exc_info=True)
