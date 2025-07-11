from .base import BaseService
from ..hands import *


class CeleryBaseService(BaseService):

    def __init__(self, queue, **kwargs):
        super().__init__(**kwargs)
        self.queue = queue
        self.num = CELERY_WORKER_COUNT
        self.autoscale = settings.CELERY_WORKER_AUTOSCALE

    @property
    def cmd(self):
        print('\n- Start Celery as Distributed Task Queue: {}'.format(self.queue.capitalize()))
        os.environ.setdefault('LC_ALL', 'C.UTF-8')
        os.environ.setdefault('PYTHONOPTIMIZE', '1')

        if os.getuid() == 0:
            os.environ.setdefault('C_FORCE_ROOT', '1')
        server_hostname = os.environ.get("SERVER_HOSTNAME")
        if not server_hostname:
            server_hostname = '%h'

        cmd = [
            'celery',
            '-A', 'server',
            'worker',
            '-P', 'threads',  # 默认的prefork是资源隔离的，导致修改settings配置时候，无法同步数据到该线程，因此需要用 threads模式
            '-l', 'INFO',
            '-c', str(self.num),
            # '--autoscale', ",".join([str(x) for x in self.autoscale]), # 开启自动弹性伸缩
            '-Q', self.queue,
            '--heartbeat-interval', '10',
            '-n', f'{self.queue}@{server_hostname}',
            '--without-mingle',
        ]
        return cmd

    @property
    def cwd(self):
        return APPS_DIR
