# -*- coding: utf-8 -*-
"""生产启动参数守护：gunicorn 的连接保持与优雅退出参数三处同源。

- 生产命令：`common/management/commands/services/services/gunicorn.py`
- 压测 CI：`.github/workflows/perf.yml`（与生产同参才可比基线）
- 文档：`docs/ops/performance-baseline.md` §三

任一处漂移都会让「压测基线」失去可比性，因此用测试锁住字面参数。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

KEEP_ALIVE = "5"
GRACEFUL_TIMEOUT = "30"


def _cmd() -> list:
    from common.management.commands.services.services.gunicorn import GunicornService

    return GunicornService(name="gunicorn", worker_gunicorn=2).cmd


class TestGunicornParams:
    def test_command_declares_keep_alive_and_graceful_timeout(self, capsys):
        """服务命令显式声明参数：不依赖 gunicorn 默认值（keep-alive 默认 2s）。"""
        cmd = " ".join(_cmd())
        assert f"--keep-alive {KEEP_ALIVE}" in cmd
        assert f"--graceful-timeout {GRACEFUL_TIMEOUT}" in cmd

    def test_perf_ci_uses_same_params(self):
        text = (ROOT / ".github/workflows/perf.yml").read_text(encoding="utf-8")
        assert f"--keep-alive {KEEP_ALIVE}" in text
        assert f"--graceful-timeout {GRACEFUL_TIMEOUT}" in text

    def test_doc_records_same_params(self):
        text = (ROOT / "docs/ops/performance-baseline.md").read_text(encoding="utf-8")
        assert f"--keep-alive {KEEP_ALIVE}" in text
        assert f"--graceful-timeout {GRACEFUL_TIMEOUT}" in text
