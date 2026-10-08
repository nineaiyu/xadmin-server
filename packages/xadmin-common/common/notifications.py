from typing import TYPE_CHECKING

from django.db.models.aggregates import Avg
from django.db.models.functions import Round
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.contracts import (
    BACKEND,
    SystemMessage,
    SystemMsgSubscription,
    UserMessage,
    get_active_superuser_queryset,
    register_message,
)
from common.settings_contract import kernel_setting

# 资源告警阈值设置键：可写面 = 监控页阈值 PUT（SecurityMonitorSerializer），逐键对应
MONITOR_THRESHOLD_SETTINGS = (
    "SECURITY_MONITOR_DISK_USED_MAX",
    "SECURITY_MONITOR_MEMORY_USED_MAX",
    "SECURITY_MONITOR_CPU_PERCENT_MAX",
    "SECURITY_MONITOR_CPU_LOAD_MAX",
)
# 阈值对账间隔（秒）：与告警检查周期一致，pubsub 丢消息后最多一个间隔内收敛
THRESHOLD_RECONCILE_INTERVAL = 60
_THRESHOLD_RECONCILE_CACHE_KEY = "monitor_thresholds_reconcile_at"


def reconcile_monitor_thresholds():
    """阈值对账：Setting 行热更靠 pubsub 回写各进程，pubsub 丢消息时本进程
    settings 停留旧值且告警判定无自愈手段。判定 / 展示前从库回读阈值应用，
    经带 TTL 的缓存闸门限流（间隔内至多一次回读），保证在收敛窗口
    （THRESHOLD_RECONCILE_INTERVAL，即告警检查周期）内自动读到新阈值；
    缓存不可用时按需要对账处理，宁可多读一次库也不让收敛失效。
    """
    from django.core.cache import cache

    from common import contracts

    try:
        if cache.get(_THRESHOLD_RECONCILE_CACHE_KEY) is not None:
            return
    except Exception:  # noqa: BLE001 缓存异常不阻断对账
        pass
    try:
        # Setting 模型经框架层契约缝消费（调用期解析）：不直接 import 业务 app，
        # 迁移期模型不可用时随本题异常处理降级（下轮再试）。
        contracts.Setting.refresh_names(MONITOR_THRESHOLD_SETTINGS)
    except Exception:  # noqa: BLE001 对账失败不阻断告警检查（下轮再试）
        return
    try:
        cache.set(_THRESHOLD_RECONCILE_CACHE_KEY, True, THRESHOLD_RECONCILE_INTERVAL)
    except Exception:  # noqa: BLE001 闸门写失败只会导致更频繁对账
        pass


@register_message
class ServerPerformanceMessage(SystemMessage):
    category = "Monitor"
    category_label = _("Monitor")
    message_type_label = _("Server performance")

    def __init__(self, terms_with_errors):
        self.terms_with_errors = terms_with_errors

    def get_html_msg(self) -> dict:
        subject = _("Server health check warning")
        context = {"terms_with_errors": self.terms_with_errors}
        message = render_to_string("monitor/msg_terminal_performance.html", context)
        return {
            "subject": subject,
            "message": message,
        }

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info["level"] = "danger"
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        admins = get_active_superuser_queryset()
        subscription.users.add(*admins)
        subscription.receive_backends = [BACKEND.EMAIL]
        subscription.save()

    def publish(self, is_async=False):
        """发布告警；收件人为空时自愈补齐活跃超管（订阅创建早于超管初始化的存量库）"""
        subscription = SystemMsgSubscription.objects.get(message_type=self.get_message_type())
        if not subscription.users.exists():
            self.post_insert_to_db(subscription)
        super().publish(is_async=is_async)

    @classmethod
    def gen_test_msg(cls):
        pass


class ServerPerformanceCheckUtil:
    # 阈值可在后台「系统设置 → 安全设置 → 资源告警」配置（settings/serializers/security.py）；
    # Setting 行经 django_ready/pubsub 回写 settings，pubsub 丢消息时由
    # reconcile_monitor_thresholds 每轮判定前回读对账，这里必须每次检查时读取
    @property
    def items_mapper(self):
        return {
            "disk_used": {
                "default": 0,
                "max_threshold": kernel_setting("SECURITY_MONITOR_DISK_USED_MAX"),
                "alarm_msg_format": _("Disk used more than {max_threshold}%: => {value}"),
            },
            "memory_used": {
                "default": 0,
                "max_threshold": kernel_setting("SECURITY_MONITOR_MEMORY_USED_MAX"),
                "alarm_msg_format": _("Memory used more than {max_threshold}%: => {value}"),
            },
            "cpu_load": {
                "default": 0,
                "max_threshold": kernel_setting("SECURITY_MONITOR_CPU_LOAD_MAX"),
                "alarm_msg_format": _("CPU load more than {max_threshold}: => {value}"),
            },
            "cpu_percent": {
                "default": 0,
                "max_threshold": kernel_setting("SECURITY_MONITOR_CPU_PERCENT_MAX"),
                "alarm_msg_format": _("CPU percent more than {max_threshold}: => {value}"),
            },
        }

    def __init__(self):
        self.terms_with_errors = []
        self.item_states = []
        self._terminals = []

    def check_and_publish(self):
        # 阈值对账：pubsub 丢消息时本进程 settings 仍是旧值，先回读再判定
        reconcile_monitor_thresholds()
        self.check()
        self.publish()
        self.sync_alert_records()

    def check(self):
        self.terms_with_errors = []
        self.item_states = []
        self.initial_terminals()

        for term in self._terminals:
            errors = self.check_terminal(term)
            if not errors:
                continue
            self.terms_with_errors.append((term, errors))

    def check_terminal(self, term):
        errors = []
        for item, data in self.items_mapper.items():
            error = self.check_item(term, item, data)
            # 无论是否超标都记录本轮状态：告警记录需要「超标建记录 / 回落置恢复」双向跃迁
            self.item_states.append(
                {
                    "item": item,
                    "value": term.get(item, data["default"]),
                    "threshold": data["max_threshold"],
                    "exceeded": bool(error),
                    "message": str(error) if error else "",
                }
            )
            if not error:
                continue
            errors.append(error)
        return errors

    def sync_alert_records(self):
        """把本轮检查结果落成告警记录（同一指标同时只保留一条未恢复记录）。

        持续超标时续写 last_time/count，回落时置 resolved；重复告警不刷记录，
        避免 60s 检查周期把告警流水打成噪音。
        """
        from common import contracts

        now = timezone.now()
        for state in self.item_states:
            value = state["value"]
            if not isinstance(value, (int, float)):
                continue
            firing = contracts.MonitorAlert.objects.filter(
                item=state["item"], status=contracts.MonitorAlert.Status.FIRING
            ).first()
            if state["exceeded"]:
                if firing:
                    firing.value = value
                    firing.threshold = state["threshold"]
                    firing.message = state["message"]
                    firing.count += 1
                    firing.last_time = now
                    firing.save(update_fields=["value", "threshold", "message", "count", "last_time"])
                else:
                    contracts.MonitorAlert.objects.create(
                        item=state["item"],
                        value=value,
                        threshold=state["threshold"],
                        message=state["message"],
                        first_time=now,
                        last_time=now,
                    )
            elif firing:
                firing.status = contracts.MonitorAlert.Status.RESOLVED
                firing.resolved_time = now
                firing.save(update_fields=["status", "resolved_time"])

    @staticmethod
    def check_item(term, item, data):
        default = data["default"]
        max_threshold = data["max_threshold"]
        value = term.get(item, default)

        if isinstance(value, bool) and value != max_threshold:
            return
        elif isinstance(value, (int, float)) and value < max_threshold:
            return
        msg = data["alarm_msg_format"]
        error = msg.format(max_threshold=max_threshold, value=value, name="api")
        return error

    def publish(self):
        if not self.terms_with_errors:
            return
        ServerPerformanceMessage(self.terms_with_errors).publish()

    @staticmethod
    def get_monitor_latest_average_value(num=3):
        """最近三次数据的平均值（Monitor 住 system 运维域，经契约缝消费）"""
        from common import contracts

        return contracts.Monitor.objects.order_by("-created_time")[0:num].aggregate(
            cpu_load=Round(Avg("cpu_load"), 2),
            cpu_percent=Round(Avg("cpu_percent"), 2),
            memory_used=Round(Avg("memory_used"), 2),
            disk_used=Round(Avg("disk_used"), 2),
        )

    def initial_terminals(self):
        self._terminals = [self.get_monitor_latest_average_value()]


class TaskMessage:
    if TYPE_CHECKING:  # 子类（任务消息）与 UserMessage 提供的属性（mixin 模式）
        subject: str
        user_display: str
        task: dict

    def get_html_msg(self) -> dict:
        context = dict(
            subject=self.subject,
            name=self.user_display,
            **self.task,
        )
        message = render_to_string("notify/msg_task.html", context)
        return {"subject": self.subject, "message": message}


@register_message
class ExportDataMessage(TaskMessage, UserMessage):
    category = "Task Message"
    category_label = _("Task Message")
    message_type_label = _("Export data message")

    def __init__(self, user, task):
        super().__init__(user)
        self.task = task
        self.subject = _("Export {} data {} message").format(self.task.get("task_name"), self.task.get("status"))


@register_message
class ImportDataMessage(TaskMessage, UserMessage):
    category = "Task Message"
    category_label = _("Task Message")
    message_type_label = _("Import data message")

    def __init__(self, user, task):
        super().__init__(user)
        self.task = task
        self.subject = _("Import {} data {} message").format(self.task.get("view_doc"), self.task.get("status"))


@register_message
class BatchDeleteDataMessage(TaskMessage, UserMessage):
    category = "Task Message"
    category_label = _("Task Message")
    message_type_label = _("Batch delete data message")

    def __init__(self, user, task):
        super().__init__(user)
        self.task = task
        self.subject = _("Batch delete {} data {} message").format(self.task.get("view_doc"), self.task.get("status"))
