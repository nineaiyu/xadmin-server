from __future__ import unicode_literals

import sys
import threading
import time

from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'common'

    def ready(self):
        from .celery import heatbeat  # noqa
        from . import signal_handlers  # noqa
        from . import tasks  # noqa
        from .signals import django_ready
        excludes = ['migrate', 'compilemessages', 'makemigrations', 'stop']
        for i in excludes:
            if i in sys.argv:
                return
        super().ready()

        def background_task():
            time.sleep(0.1)
            django_ready.send(CommonConfig)

        threading.Thread(target=background_task, daemon=True).start()
