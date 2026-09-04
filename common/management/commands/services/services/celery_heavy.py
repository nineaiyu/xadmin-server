from .celery_base import CeleryBaseService

__all__ = ['CeleryHeavyService']


class CeleryHeavyService(CeleryBaseService):
    """heavy 队列 worker：消费导入/导出/批量操作等重任务（见 CELERY_TASK_ROUTES）"""

    def __init__(self, **kwargs):
        kwargs['queue'] = 'heavy'
        super().__init__(**kwargs)
