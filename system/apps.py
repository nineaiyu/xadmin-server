from django.apps import AppConfig


class SystemConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'system'

    def ready(self):
        from . import signal_handler  # noqa
        from . import signal_task_execution  # noqa
        super().ready()
