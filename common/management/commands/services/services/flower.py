from .base import BaseService
from ..hands import *

__all__ = ['FlowerService']


class FlowerService(BaseService):
    # https://flower.readthedocs.io/en/latest/man.html?highlight=pool#description
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @property
    def db_file(self):
        return os.path.join(LOG_DIR, 'flower.db')

    @property
    def cmd(self):
        print("\n- Start Flower as Task Monitor")

        if os.getuid() == 0:
            os.environ.setdefault('C_FORCE_ROOT', '1')
        cmd = [
            'celery',
            '-A', 'server',
            'flower',
            '-logging=info',
            '--url_prefix=api/flower',
            '--auto_refresh=False',
            '--max_tasks=1000',
            '--persistent=True',
            '--state_save_interval=600000',
            f'--basic-auth={CELERY_FLOWER_AUTH}',  # 注释则代表 flower 只读权限
            f'-db={self.db_file}',
            '--state_save_interval=600000',
            f'--address={CELERY_FLOWER_HOST}',
            f'--port={CELERY_FLOWER_PORT}',
        ]
        if settings.DEBUG:
            cmd += ['--debug']
        return cmd

    @property
    def cwd(self):
        return APPS_DIR
