from .base import BaseService
from ..hands import *

__all__ = ['BeatService']


class BeatService(BaseService):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @property
    def cmd(self):
        scheduler = "django_celery_beat.schedulers:DatabaseScheduler"
        print("\n- Start Beat as Periodic Task Scheduler")
        cmd = [
            'celery', '-A',
            'server', 'beat',
            '-l', 'INFO',
            '--scheduler', scheduler,
            '--max-interval', '60'
        ]
        return cmd

    @property
    def cwd(self):
        return APPS_DIR
