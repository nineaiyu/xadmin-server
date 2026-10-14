#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""容器优雅停止：停止信号直达服务进程，并被转发给子进程。

背景（实测）：entrypoint 以 `bash entrypoint.sh` 启动、又以子进程方式跑
`python manage.py start X`——bash 不转发信号，内核也不对 PID 1 施加信号的默认动作，
docker stop 发出的 SIGQUIT 被丢弃，容器只能等 stop_grace_period 到点后 SIGKILL：
空闲的 celery-heavy 也要等满 300s、celery-worker 120s 才 Stopped。守护两层修复：

- entrypoint 最终启动用 exec，服务进程成为容器 PID 1（信号可达，见
  test_scale_out_assets.py 的源码级断言）；
- 服务监督进程注册 SIGTERM/SIGQUIT：转发给子进程后**无时限**等待（celery warm
  shutdown 跑完在途任务、gunicorn 等在途请求），上限交给 docker 宽限期后的
  SIGKILL 兜底；第二次信号直接强杀；命令行 stop 保持 STOP_TIMEOUT 兜底不挂起。
"""

import os
import re
import signal
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# 收到 SIGTERM 后再花 0.8s 收尾才退出（模拟 warm shutdown 跑完在途任务）；
# 装好信号处理器后写就绪标记，避免「子进程还没就绪就被 kill」的竞态
SLOW_EXIT_ON_TERM = """
import signal, sys, time
def _term(*_args):
    time.sleep(0.8)
    sys.exit(0)
signal.signal(signal.SIGTERM, _term)
open(sys.argv[1], "w").write("ready")
time.sleep(60)
"""

# 完全忽略 SIGTERM（模拟卡死的子进程）
IGNORE_TERM = """
import signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open(sys.argv[1], "w").write("ready")
time.sleep(60)
"""


class _FakeService:
    """只记录 stop 调用形态的假服务。"""

    def __init__(self):
        self.calls: list[dict] = []

    def stop(self, force: bool = False, wait_forever: bool = False) -> None:
        self.calls.append({"force": force, "wait_forever": wait_forever})


def _make_util(monkeypatch, services):
    from common.management.commands.services.utils import ServicesUtil

    util = ServicesUtil(services=services)
    exits: list[int] = []
    # exit_process 是容器进程的终点：测试里替换为记录（实例属性不参与描述符绑定）
    monkeypatch.setattr(util, "exit_process", lambda: exits.append(0))
    return util, exits


class TestStopSignalHandling:
    def test_handlers_registered_for_sigterm_and_sigquit(self, monkeypatch):
        """SIGTERM/SIGQUIT 都必须注册处理函数：PID 1 没有处理函数时信号被内核丢弃。"""
        from common.management.commands.services import utils as services_utils

        installed: dict[int, object] = {}
        fake_signal = types.SimpleNamespace(
            SIGTERM=signal.SIGTERM,
            SIGQUIT=signal.SIGQUIT,
            signal=lambda sig, handler: installed.setdefault(sig, handler),
        )
        monkeypatch.setattr(services_utils, "signal", fake_signal)

        util = services_utils.ServicesUtil(services=[])
        util.install_stop_signal_handlers()

        assert installed == {
            signal.SIGTERM: util._handle_stop_signal,
            signal.SIGQUIT: util._handle_stop_signal,
        }

    def test_stop_signal_stops_children_with_unbounded_wait(self, monkeypatch):
        """首次停止信号：转发给全部子进程并等待其自行退出，然后退出容器进程。"""
        services = [_FakeService(), _FakeService()]
        util, exits = _make_util(monkeypatch, services)

        util._handle_stop_signal(signal.SIGQUIT, None)

        assert [s.calls for s in services] == [
            [{"force": False, "wait_forever": True}],
            [{"force": False, "wait_forever": True}],
        ]
        assert util.EXIT_EVENT.is_set()
        assert exits == [0]

    def test_second_stop_signal_forces_immediate_exit(self, monkeypatch):
        """第二次停止信号：不再等待，强杀子进程后立即退出（人工加速路径）。"""
        service = _FakeService()
        util, exits = _make_util(monkeypatch, [service])

        util._handle_stop_signal(signal.SIGQUIT, None)
        util._handle_stop_signal(signal.SIGQUIT, None)

        assert service.calls == [
            {"force": False, "wait_forever": True},
            {"force": True, "wait_forever": False},
        ]
        assert exits == [0, 0]

    def test_container_stop_signal_is_handled(self):
        """Dockerfile 的 STOPSIGNAL 必须是监督进程已注册的信号（防止改信号后静默失联）。"""
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        signal_name = re.search(r"STOPSIGNAL (\w+)", text).group(1)
        assert signal_name in {"SIGTERM", "SIGQUIT"}


def _make_probe_service(pid_path: Path, log_path: Path, script: str, marker_path: str):
    """构造带真实子进程的探针服务：只用 BaseService 的启停逻辑，路径落在 tmp_path。

    BaseService 在函数内导入（模块级导入会连带 hands 读取运行时配置）。
    """
    from common.management.commands.services.services.base import BaseService

    class _ProbeService(BaseService):
        def __init__(self) -> None:
            super().__init__(name="probe")
            self._pid_path = pid_path
            self._log_path = log_path
            self._script = script
            self._marker_path = marker_path

        @property
        def cmd(self):
            return [sys.executable, "-c", self._script, self._marker_path]

        @property
        def cwd(self):
            return str(self._log_path.parent)

        @property
        def pid_filepath(self):
            return str(self._pid_path)

        @property
        def log_filepath(self):
            return str(self._log_path)

    return _ProbeService()


class TestBaseServiceStopWaiting:
    def _start(self, tmp_path: Path, script: str):
        marker = tmp_path / "probe.ready"
        service = _make_probe_service(tmp_path / "probe.pid", tmp_path / "probe.log", script, str(marker))
        service.start()
        assert service.is_running
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "子进程未就绪：信号处理器安装前被 kill 会让用例变成竞态"
        return service

    @staticmethod
    def _reap(service) -> None:
        try:
            if service.is_running:
                os.kill(service.pid, signal.SIGKILL)
        except OSError:
            pass

    def test_wait_forever_waits_child_finish(self, tmp_path):
        """wait_forever=True：STOP_TIMEOUT 已到也不提前返回，等子进程收尾完再报 Ok。"""
        service = self._start(tmp_path, SLOW_EXIT_ON_TERM)
        service.STOP_TIMEOUT = 0  # 有界等待会立刻判 Error，用来证明无时限等待确实生效
        try:
            started = time.monotonic()
            service.stop(wait_forever=True)
            elapsed = time.monotonic() - started
            assert not service.is_running
            assert not (tmp_path / "probe.pid").exists()
            assert elapsed >= 0.6, "必须等子进程（SIGTERM 后 0.8s 退出）真正结束"
        finally:
            self._reap(service)

    def test_bounded_stop_keeps_cli_semantics(self, tmp_path):
        """命令行 stop（wait_forever=False）保持 STOP_TIMEOUT 兜底：不无限挂起、不动 pid。"""
        service = self._start(tmp_path, IGNORE_TERM)
        service.STOP_TIMEOUT = 1
        try:
            service.stop()
            assert service.is_running
            assert (tmp_path / "probe.pid").exists()
        finally:
            self._reap(service)

    def test_force_stop_kills_stubborn_child(self, tmp_path):
        """force=True（第二次信号路径）用 SIGKILL 强杀忽略 SIGTERM 的子进程。"""
        service = self._start(tmp_path, IGNORE_TERM)
        service.STOP_TIMEOUT = 5
        try:
            service.stop(force=True)
            assert not service.is_running
            assert not (tmp_path / "probe.pid").exists()
        finally:
            self._reap(service)
