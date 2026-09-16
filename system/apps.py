from django.apps import AppConfig


class SystemConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "system"

    def ready(self):
        from . import signal_handler  # noqa
        from . import signal_task_execution  # noqa
        from .utils.dform_flow import register_approval_handlers  # noqa

        # 动态表单提交的「审批通过后自动落库」动作：进程启动时注册一次
        register_approval_handlers()
        super().ready()
