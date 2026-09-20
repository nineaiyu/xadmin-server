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

        # modules 命令自身会做模块配置校验，且提供 --clear-override 恢复通道：
        # 若在此处先行 fail-fast，覆盖行引用已移除模块时将无法执行恢复命令。
        # doctor 同理：它是诊断入口，非法模块配置必须由它「报出 + 给修复命令」，
        # 在 setup 阶段抛 ImproperlyConfigured 会让 doctor 直接跑不起来（只剩裸 traceback）。
        excludes = ["migrate", "compilemessages", "makemigrations", "stop", "modules", "doctor"]
        for i in excludes:
            if i in sys.argv:
                return
        super().ready()

        # 功能模块裁剪：校验部署基线（未知模块/内核被关/依赖未满足 → 启动期 fail-fast）。
        # 后台覆盖层（DB 单行）的解析与受裁剪影响的缓存清理推迟到首次实际使用：
        # 此处访问数据库会建立指向「尚未创建的测试库」的连接，破坏测试库创建。
        from .core.modules import validate_deployment_config

        validate_deployment_config()

        def background_task():
            time.sleep(0.1)
            django_ready.send(CommonConfig)

        threading.Thread(target=background_task, daemon=True).start()
