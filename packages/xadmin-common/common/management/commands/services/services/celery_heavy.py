from django.conf import settings

from .celery_base import CeleryBaseService

__all__ = ["CeleryHeavyService"]


class CeleryHeavyService(CeleryBaseService):
    """heavy 队列 worker：消费导入/导出/批量操作等重任务（见 CELERY_TASK_ROUTES）

    prefetch 固定为 1，避免单线程囤积长任务造成其他任务饥饿；
    池类型与并发数可配（CELERY_HEAVY_POOL / CELERY_HEAVY_CONCURRENCY）——
    CPU 密集的 Excel 导出可改 'prefork' 提升吞吐，默认 threads 与
    default 队列保持相同的运行时状态共享行为。
    """

    def __init__(self, **kwargs):
        kwargs["queue"] = "heavy"
        kwargs.setdefault("pool", settings.CELERY_HEAVY_POOL)
        kwargs.setdefault("concurrency", settings.CELERY_HEAVY_CONCURRENCY)
        kwargs["prefetch"] = 1
        super().__init__(**kwargs)
