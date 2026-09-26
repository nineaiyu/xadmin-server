from django.apps import AppConfig


class AiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai"
    verbose_name = "AI 平台与知识库"

    def ready(self):
        # AI 周期任务（用量账本保留期清理）经 register_as_period_task 注册到
        # django_celery_beat：必须显式 import 任务模块才会执行装饰器完成注册
        from . import tasks  # noqa: F401
