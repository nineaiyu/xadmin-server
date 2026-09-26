from django.apps import AppConfig


class DatasetConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dataset"
    verbose_name = "数据分析与动态表单"

    def ready(self):
        # 动态表单提交的「审批通过后自动落库」动作：进程启动时注册一次
        from .utils.dform_flow import register_approval_handlers

        register_approval_handlers()

        # 定时报表周期任务（dispatch_cron_reports 等）经 register_as_period_task
        # 装饰器注册到 django_celery_beat：必须显式 import 任务模块才会完成注册
        from . import analysis_tasks  # noqa: F401
