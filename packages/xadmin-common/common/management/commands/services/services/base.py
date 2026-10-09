import abc
import datetime
import os
import shutil
import signal
import subprocess
import threading
import time
from typing import Any

import psutil

from ..hands import *


class BaseService(abc.ABC):
    def __init__(self, **kwargs: Any) -> None:
        self.name = kwargs["name"]
        self._process: Any = None
        self._log_stream: Any = None
        # 上一次日志轮转的日期（按自然日触发，替代此前"精确命中 23:59 分钟"的判定：
        # 监督循环 30s 一次且可能被重启打断，原判定大概率整日不命中）
        self._last_rotate_date: str | None = None
        self.STOP_TIMEOUT = 10
        self.max_retry = 3
        self.retry = 0
        self.LOG_KEEP_DAYS = 7
        self.EXIT_EVENT = threading.Event()

    @property
    @abc.abstractmethod
    def cmd(self) -> Any:
        return []

    @property
    @abc.abstractmethod
    def cwd(self) -> str:
        return ""

    @property
    def is_running(self) -> bool:
        if self.pid == 0:
            return False
        try:
            os.kill(self.pid, 0)
        except (OSError, ProcessLookupError):
            return False
        else:
            return True

    def show_status(self) -> None:
        if self.is_running:
            msg = f"{self.name} is running: {self.pid}."
        else:
            msg = f"{self.name} is stopped."
            if DEBUG:
                msg = (
                    "\033[31m{} is stopped.\033[0m\nYou can manual start it to find the error: \n"
                    "  $ cd {}\n"
                    "  $ {}".format(self.name, self.cwd, " ".join(self.cmd))
                )

        print(msg)

    # -- log --
    @property
    def log_filename(self) -> str:
        return f"{self.name}.log"

    @property
    def log_filepath(self) -> Any:
        return os.path.join(LOG_DIR, self.log_filename)

    @property
    def log_file(self) -> Any:
        # 句柄缓存复用：原实现每次访问都 open 且从不关闭，一次启动/重启泄漏约 4 个 fd；
        # 路径变化（如测试替换 LOG_DIR）时重新打开，追加模式下多进程共享句柄是安全的
        path = self.log_filepath
        if self._log_stream is None or self._log_stream.name != path:
            self._log_stream = open(path, "a")
        return self._log_stream

    @property
    def log_dir(self) -> Any:
        return os.path.dirname(self.log_filepath)

    # -- end log --

    # -- pid --
    @property
    def pid_filepath(self) -> Any:
        return os.path.join(TMP_DIR, f"{self.name}.pid")

    @property
    def pid(self) -> Any:
        if not os.path.isfile(self.pid_filepath):
            return 0
        with open(self.pid_filepath) as f:
            try:
                pid = int(f.read().strip())
            except ValueError:
                pid = 0
        return pid

    def write_pid(self) -> None:
        with open(self.pid_filepath, "w") as f:
            f.write(str(self.process.pid))

    def remove_pid(self) -> None:
        if os.path.isfile(self.pid_filepath):
            os.unlink(self.pid_filepath)

    # -- end pid --

    # -- process --
    @property
    def process(self) -> Any:
        if not self._process:
            try:
                self._process = psutil.Process(self.pid)
            except Exception:
                # 进程句柄获取失败：保持 None（由调用方判 running 分支）
                pass
        return self._process

    # -- end process --

    # -- action --
    def open_subprocess(self) -> None:
        kwargs = {"cwd": self.cwd, "stderr": self.log_file, "stdout": self.log_file}
        self._process = subprocess.Popen(self.cmd, **kwargs)

    def start(self) -> None:
        if self.is_running:
            self.show_status()
            return
        # pid 文件可能仍指向在跑的旧进程（如其他容器误删 pid 文件导致 watcher
        # 误判已停止）：先终止旧进程再拉起，避免同名 worker 堆积引发
        # DuplicateNodenameWarning（控制广播收到同一节点多个回复）
        self._terminate_stale()
        self.remove_pid()
        self.open_subprocess()
        self.write_pid()
        self.start_other()

    def _terminate_stale(self) -> None:
        pid = self.pid
        if pid <= 0:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        for _ in range(self.STOP_TIMEOUT):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(1)
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def start_other(self) -> None:  # noqa: B027 可选钩子：默认无操作，子类按需覆写
        pass

    def stop(self, force: bool = False) -> None:
        if not self.is_running:
            self.show_status()
            # self.remove_pid()
            return

        print(f"Stop service: {self.name}", end="")
        sig = 9 if force else 15
        os.kill(self.pid, sig)

        if self.process is None:
            print("\033[31m No process found\033[0m")
            return
        try:
            self.process.wait(1)
        except Exception:
            # wait 超时/进程已退出：忽略（后续轮询判定终态）
            pass

        # 轮询等待进程退出：原实现循环体内没有 sleep，10 次判定在微秒内跑完，
        # 进程还在时直接报 Error 并残留 pid 文件（后续 start 的状态判定随之混乱）
        deadline = time.monotonic() + self.STOP_TIMEOUT
        while True:
            if not self.is_running:
                print("\033[32m Ok\033[0m")
                self.remove_pid()
                return
            if time.monotonic() >= deadline:
                print("\033[31m Error\033[0m")
                return
            time.sleep(0.2)

    def watch(self) -> None:
        self._check()
        if self.is_running:
            # retry 是「连续失败计数」：服务稳定运行即复位，避免历史瞬时重启累计
            # 触发 max_retry 后整个监督进程 clean_up（此前计数只增不减）
            self.retry = 0
        else:
            self._restart()
        self._rotate_log()

    def _check(self) -> None:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{now} Check service status: {self.name} -> ", end="")
        if self.process:
            try:
                self.process.wait(1)  # 不wait，子进程可能无法回收
            except Exception:
                # wait 异常（进程不存在等）：忽略，状态判定走下方 is_running
                pass

        if self.is_running:
            print(f"running at {self.pid}")
        else:
            print(f"stopped at {self.pid}")

    def _restart(self) -> None:
        if self.retry > self.max_retry:
            print(f"Service start failed, exit: {self.name}")
            self.EXIT_EVENT.set()
            return
        self.retry += 1
        print(f"> Find {self.name} stopped, retry {self.retry}, {self.pid}")
        self.start()

    def _rotate_log(self) -> None:
        """按自然日轮转日志：跨天后把当前日志归档到上一日目录并清空。"""
        now = datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        if self._last_rotate_date is None:
            # 首次监督可能发生在任意时刻：只登记日期，避免服务启动即归档当日日志
            self._last_rotate_date = today
            return
        if self._last_rotate_date == today:
            return

        backup_date = self._last_rotate_date
        self._last_rotate_date = today
        backup_log_dir = os.path.join(self.log_dir, backup_date)
        if not os.path.exists(backup_log_dir):
            os.mkdir(backup_log_dir)

        backup_log_path = os.path.join(backup_log_dir, self.log_filename)
        if os.path.isfile(self.log_filepath) and not os.path.isfile(backup_log_path):
            print(f"Rotate log file: {self.log_filepath} => {backup_log_path}")
            shutil.copy(self.log_filepath, backup_log_path)
            with open(self.log_filepath, "w"):
                pass

        to_delete_date = now - datetime.timedelta(days=self.LOG_KEEP_DAYS)
        to_delete_dir = os.path.join(LOG_DIR, to_delete_date.strftime("%Y-%m-%d"))
        if os.path.exists(to_delete_dir):
            print(f"Remove old log: {to_delete_dir}")
            shutil.rmtree(to_delete_dir, ignore_errors=True)

    # -- end action --
