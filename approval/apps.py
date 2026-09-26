from django.apps import AppConfig


class ApprovalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "approval"
    verbose_name = "审批流"

    def ready(self):
        # 审批周期任务（过期/提醒/清理）经 register_as_period_task 装饰器注册到
        # django_celery_beat：必须显式 import 任务模块才会执行装饰器完成注册
        from . import tasks  # noqa: F401
