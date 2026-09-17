import sys
import threading
import time

from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "common"

    def ready(self):
        from .celery import heatbeat  # noqa
        from .celery import failure_handler  # noqa
        from .celery import metrics as celery_metrics  # noqa
        from . import backup_alert  # noqa
        from . import signal_handlers  # noqa
        from . import tasks  # noqa
        from .signals import django_ready

        excludes = ["migrate", "compilemessages", "makemigrations", "stop"]
        for i in excludes:
            if i in sys.argv:
                return
        super().ready()

        # 功能模块裁剪：校验模块配置（未知模块/内核被关/依赖未满足 → 启动期 fail-fast），
        # 并在存在停用模块时清理菜单/权限缓存（配置变更需重启，重启清理一次即可）
        from .core.modules import invalidate_trimmed_caches, resolve_modules

        resolve_modules()
        invalidate_trimmed_caches()

        def background_task():
            time.sleep(0.1)
            django_ready.send(CommonConfig)

        threading.Thread(target=background_task, daemon=True).start()
