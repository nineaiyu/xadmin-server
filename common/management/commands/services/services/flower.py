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

        if not CELERY_FLOWER_AUTH:
            # 未配置认证时仅允许本机回环地址，避免无认证监控面板暴露到外网
            if CELERY_FLOWER_HOST not in ('127.0.0.1', 'localhost'):
                print(
                    "\n- CELERY_FLOWER_AUTH 未配置时 Flower 仅允许绑定 127.0.0.1，"
                    "生产环境请在 config.yml 配置 CELERY_FLOWER_AUTH（格式 用户:密码）"
                )
                sys.exit(11)
            print("\n- CELERY_FLOWER_AUTH 未配置，Flower 将无认证启动且仅绑定 127.0.0.1（仅供本机调试）")

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
            f'-db={self.db_file}',
            '--state_save_interval=600000',
            f'--address={CELERY_FLOWER_HOST}',
            f'--port={CELERY_FLOWER_PORT}',
        ]
        if CELERY_FLOWER_AUTH:
            cmd.append(f'--basic-auth={CELERY_FLOWER_AUTH}')  # 未配置则代表 flower 无认证（仅限本机回环）
        if settings.DEBUG:
            cmd += ['--debug']
        return cmd

    @property
    def cwd(self):
        return APPS_DIR
