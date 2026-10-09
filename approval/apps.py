from django.apps import AppConfig


class ApprovalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "approval"
    verbose_name = "审批流"

    def ready(self) -> None:
        # 审批周期任务（过期/提醒/清理）经 register_as_period_task 装饰器注册到
        # django_celery_beat：必须显式 import 任务模块才会执行装饰器完成注册
        # 终态回写接收器（approval_instance_finished → 业务同步器）在此注册
        from . import (
            signal_handler,  # noqa: F401
            tasks,  # noqa: F401
        )
