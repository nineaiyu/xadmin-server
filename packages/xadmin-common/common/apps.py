import os
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

        # 契约注入装配：外置分发包经 entry points 注册契约提供方。
        # 位于修复命令早退之后（migrate/doctor 不装配，broken 提供方不堵修复
        # 通道）；此时全部业务 app ready() 已完成、URLConf 未加载——common 是
        # INSTALLED_APPS 末位的 django ready，即框架层最晚的统一装配点（需要
        # 更早生效的注入走二开自身 app 的 ready() 注册）。
        from .contracts import load_contract_entry_points

        load_contract_entry_points()

        # 功能模块裁剪：校验部署基线（未知模块/内核被关/依赖未满足 → 启动期 fail-fast）。
        # 后台覆盖层（DB 单行）的解析与受裁剪影响的缓存清理推迟到首次实际使用：
        # 此处访问数据库会建立指向「尚未创建的测试库」的连接，破坏测试库创建。
        from .core.modules import validate_deployment_config

        validate_deployment_config()

        def background_task():
            time.sleep(0.1)
            django_ready.send(CommonConfig)

        # pytest 进程下不启动：测试进程的 DB 访问由 pytest-django 阻断器统一管控，
        # 该线程的早期查询只会以阻断异常告终（sqlite 档表现为线程告警噪音）；而在
        # PG nightly 档（tests/settings_pg.py），Django 的 pool property「读即建池」
        # 且发生在阻断点（ensure_connection）之前，线程会把连接池固化到「尚未创建的
        # 测试库名」上，毒化后续全部用例（2026-10-01 首轮 nightly 3630 errors 根因，
        # 处置见 docs/plans/容器化PG-nightly测试档立项-2026.10.md §五）。
        # E2E 的 daphne 子进程不经 pytest 启动，不受影响；直发 django_ready 的
        # 测试（test_signal_handlers.py）也不经本线程，行为不变。
        if any("pytest" in arg for arg in sys.argv) or "PYTEST_XDIST_WORKER" in os.environ:
            return
        threading.Thread(target=background_task, daemon=True).start()
