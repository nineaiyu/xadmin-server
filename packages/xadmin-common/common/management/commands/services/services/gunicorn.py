from common.startup import CoreTerminal

from ..hands import *
from .base import BaseService

__all__ = ["GunicornService"]


class GunicornService(BaseService):
    def __init__(self, **kwargs):
        self.worker = kwargs["worker_gunicorn"]
        super().__init__(**kwargs)

    @property
    def cmd(self):
        print("\n- Start Gunicorn ASGI HTTP Server")

        log_format = '%(h)s %(t)s %(L)ss "%(r)s" %(s)s %(b)s '
        bind = f"{HTTP_HOST}:{HTTP_PORT}"

        cmd = [
            "gunicorn",
            "server.asgi:application",
            "-b",
            bind,
            # uvicorn.workers 自 0.30 起弃用，官方迁至独立包 uvicorn-worker
            "-k",
            "uvicorn_worker.UvicornWorker",
            "-w",
            str(self.worker),
            "--max-requests",
            "10240",
            "--max-requests-jitter",
            "2048",
            # 显式声明连接与退出参数（勿依赖默认值）：
            # - keep-alive 5s（默认 2s）：L4 nginx 与前端复用连接，5s 减少握手开销；
            # - graceful-timeout 30s：滚动重建/缩容时等在途请求跑完再退出——UvicornWorker
            #   下 --timeout 语义弱化（请求超时由底座自管），优雅退出只能靠它控制。
            # 压测基线（.github/workflows/perf.yml、docs/ops/performance-baseline.md）
            # 使用同一组参数，否则基线不可比。
            "--keep-alive",
            "5",
            "--graceful-timeout",
            "30",
            "--access-logformat",
            log_format,
            "--access-logfile",
            "-",
        ]
        if DEBUG:
            cmd.append("--reload")
        return cmd

    @property
    def cwd(self):
        return APPS_DIR

    def start_other(self):
        core_terminal = CoreTerminal()
        core_terminal.start_heartbeat_thread()
