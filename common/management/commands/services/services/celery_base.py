from .base import BaseService
from ..hands import *


class CeleryBaseService(BaseService):
    def __init__(self, queue, pool=None, concurrency=None, prefetch=None, **kwargs):
        super().__init__(**kwargs)
        self.queue = queue
        self.num = int(concurrency or CELERY_WORKER_COUNT)
        self.pool = pool or "threads"
        self.prefetch = prefetch
        self.autoscale = settings.CELERY_WORKER_AUTOSCALE

    @property
    def cmd(self):
        print("\n- Start Celery as Distributed Task Queue: {}".format(self.queue.capitalize()))
        os.environ.setdefault("LC_ALL", "C.UTF-8")
        os.environ.setdefault("PYTHONOPTIMIZE", "1")

        if os.getuid() == 0:
            os.environ.setdefault("C_FORCE_ROOT", "1")
        server_hostname = os.environ.get("SERVER_HOSTNAME")
        if not server_hostname:
            server_hostname = "%h"

        cmd = [
            "celery",
            "-A",
            "server",
            "worker",
            # 默认的prefork是资源隔离的，导致修改settings配置时候，无法同步数据到该线程，因此需要用 threads模式；
            # 池类型按队列可配（CELERY_HEAVY_POOL 等），CPU 密集队列可显式改用 prefork
            "-P",
            self.pool,
            "-l",
            "INFO",
            "-c",
            str(self.num),
            # '--autoscale', ",".join([str(x) for x in self.autoscale]), # 开启自动弹性伸缩
            "-Q",
            self.queue,
            "--heartbeat-interval",
            "10",
            "-n",
            f"{self.queue}@{server_hostname}",
            "--without-mingle",
        ]
        # 长任务队列固定 prefetch=1，避免单线程囤积任务造成其他任务饥饿
        if self.prefetch is not None:
            cmd += ["--prefetch", str(self.prefetch)]
        return cmd

    @property
    def cwd(self):
        return APPS_DIR
