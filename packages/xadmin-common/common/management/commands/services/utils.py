import os
import signal
import sys
import threading
import time
from typing import Any

import daemon
from daemon import pidfile

from .hands import *
from .services.base import BaseService


class ServicesUtil:
    def __init__(
        self, services: list[BaseService], run_daemon: bool = False, force_stop: bool = False, stop_daemon: bool = False
    ) -> None:
        self._services = services
        self.run_daemon = run_daemon
        self.force_stop = force_stop
        self.stop_daemon = stop_daemon
        self.EXIT_EVENT = threading.Event()
        self.check_interval = 30
        self.files_preserve_map: dict[str, str] = {}
        self._daemon_log_file: Any = None
        self._stop_signal_received = False

    def restart(self) -> None:
        self.stop()
        time.sleep(5)
        self.start_and_watch()

    def start_and_watch(self) -> None:
        print(time.ctime())
        print("server now start")
        self.start()
        if self.run_daemon:
            self.show_status()
            with self.daemon_context:
                self.watch()
        else:
            self.install_stop_signal_handlers()
            self.watch()

    def start(self) -> None:
        check_db_status = False
        if "gunicorn" in [service.name for service in self._services]:
            server_prepare()
            check_db_status = True
        if not check_db_status and {"celery_default", "beat", "celery_heavy"} & set(
            [service.name for service in self._services]
        ):
            celery_prepare()
        for service in self._services:
            service.start()
            self.files_preserve_map[service.name] = service.log_file

        time.sleep(1)

    def stop(self, force: bool = False, wait_forever: bool = False) -> None:
        for service in self._services:
            service.stop(force=force or self.force_stop, wait_forever=wait_forever)

        if self.stop_daemon:
            self._stop_daemon()

    # -- 容器停止信号 --
    def install_stop_signal_handlers(self) -> None:
        """注册容器停止信号处理：docker stop 发 SIGQUIT（见 Dockerfile STOPSIGNAL）。

        entrypoint 以 exec 让本进程成为容器 PID 1，因而必须显式注册：内核不对 PID 1
        施加信号的默认动作（没有处理函数时信号被丢弃），否则 docker 只能等
        stop_grace_period 到点后 SIGKILL——空闲容器也要等满宽限期。
        """
        signal.signal(signal.SIGTERM, self._handle_stop_signal)
        signal.signal(signal.SIGQUIT, self._handle_stop_signal)

    def _handle_stop_signal(self, signum: int, _frame: Any) -> None:
        if self._stop_signal_received:
            # 第二次信号：不再等待（人工加速），强杀子进程后立即退出
            print(f"Received signal {signum} again, force stop services", flush=True)
            self.stop(force=True)
            self.exit_process()
            return
        self._stop_signal_received = True
        print(f"Received signal {signum}, stop services", flush=True)
        # 信号处理函数运行在主线程：这里同步收尾后直接退出。不能让 handler 返回后
        # 由 watch 循环收尾——PEP 475 会在 handler 未抛异常时按剩余时长续睡
        # （check_interval 30s），容器迟迟不退出。
        self.EXIT_EVENT.set()
        # 无时限等待子进程自行退出（celery warm shutdown 跑完在途任务、gunicorn 等在途
        # 请求），上限由 docker stop_grace_period 到点后的 SIGKILL 兜底
        self.stop(wait_forever=True)
        self.exit_process()

    @staticmethod
    def exit_process() -> None:
        # 容器日志经 stdout 管道（非 tty）是块缓冲：先刷缓冲再退出，避免收尾日志丢失
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    # -- watch --
    def watch(self) -> None:
        while not self.EXIT_EVENT.is_set():
            try:
                _exit = self._watch()
                if _exit:
                    break
                time.sleep(self.check_interval)
            except KeyboardInterrupt:
                print("Start stop services")
                break
        self.clean_up()

    def _watch(self) -> bool:
        for service in self._services:
            service.watch()
            if service.EXIT_EVENT.is_set():
                self.EXIT_EVENT.set()
                return True
        return False

    # -- end watch --

    def clean_up(self) -> None:
        if not self.EXIT_EVENT.is_set():
            self.EXIT_EVENT.set()
        self.stop()

    def show_status(self) -> None:
        for service in self._services:
            service.show_status()

    # -- daemon --
    def _stop_daemon(self) -> None:
        if self.daemon_pid and self.daemon_is_running:
            os.kill(self.daemon_pid, 15)
        self.remove_daemon_pid()

    def remove_daemon_pid(self) -> None:
        if os.path.isfile(self.daemon_pid_filepath):
            os.unlink(self.daemon_pid_filepath)

    @property
    def daemon_pid(self) -> Any:
        if not os.path.isfile(self.daemon_pid_filepath):
            return 0
        with open(self.daemon_pid_filepath) as f:
            try:
                pid = int(f.read().strip())
            except ValueError:
                pid = 0
        return pid

    @property
    def daemon_is_running(self) -> bool:
        try:
            os.kill(self.daemon_pid, 0)
        except (OSError, ProcessLookupError):
            return False
        else:
            return True

    @property
    def daemon_pid_filepath(self) -> Any:
        return os.path.join(TMP_DIR, "server.pid")

    @property
    def daemon_log_filepath(self) -> Any:
        return os.path.join(LOG_DIR, "server.log")

    @property
    def daemon_context(self) -> Any:
        # 句柄缓存复用：原实现每次访问都 open 且从不关闭（restart 路径逐个泄漏 fd）
        if self._daemon_log_file is None:
            self._daemon_log_file = open(self.daemon_log_filepath, "a")
        context = daemon.DaemonContext(
            pidfile=pidfile.TimeoutPIDLockFile(self.daemon_pid_filepath),
            signal_map={
                signal.SIGTERM: lambda x, y: self.clean_up(),
                signal.SIGHUP: "terminate",
            },
            stdout=self._daemon_log_file,
            stderr=self._daemon_log_file,
            files_preserve=list(self.files_preserve_map.values()),
            detach_process=True,
        )
        return context

    # -- end daemon --
