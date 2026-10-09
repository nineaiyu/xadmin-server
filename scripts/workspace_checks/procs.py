# -*- coding: utf-8 -*-
"""子进程执行封装：只读调用既有单仓门禁脚本（node / bash / python）。

关键口径：

- **绝不吞掉异常转为 skip**——可执行文件缺失记为 ``tool_missing``（degraded），
  超时记为超时失败，非零退出码原样上抛为 fail；
- 既有脚本以 ``[skip]`` 前缀声明「单仓检出缺依赖」，调用方据此判 missing 而非 pass。
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field

from . import report

SKIP_MARKER = "[skip]"


@dataclass
class ProcResult:
    cmd: list
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    tool_missing: bool = False
    timed_out: bool = False
    output: list = field(default_factory=list)

    @property
    def skipped(self) -> bool:
        return SKIP_MARKER in (self.stdout + self.stderr)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.tool_missing and not self.timed_out


def run(cmd: list, cwd=None, env=None, timeout: float = 900.0) -> ProcResult:
    """执行命令并捕获输出；可执行文件缺失/超时以结构化字段返回，不抛异常。"""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [str(item) for item in cmd],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return ProcResult(cmd=list(cmd), returncode=None, seconds=time.monotonic() - started, tool_missing=True)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return ProcResult(
            cmd=list(cmd),
            returncode=None,
            stdout=out,
            stderr=err,
            seconds=time.monotonic() - started,
            timed_out=True,
        )
    return ProcResult(
        cmd=list(cmd),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        seconds=time.monotonic() - started,
    )


def tail(text: str, limit: int = 20) -> list:
    """取输出的末若干非空行（作为 findings 证据）。"""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return lines[-limit:]


def run_gate(
    face: str,
    check_id: str,
    cmd: list,
    cwd=None,
    env=None,
    repo: str | None = None,
    timeout: float = 900.0,
    allowed: bool = False,
    success_note: str = "",
) -> report.CheckResult:
    """执行一个门禁命令并归一为 CheckResult（缺失=degraded，[skip]=missing，非零=fail）。"""
    result = run(cmd, cwd=cwd, env=env, timeout=timeout)
    base = {"face": face, "id": check_id, "repo": repo, "seconds": result.seconds}
    if result.tool_missing:
        return report.CheckResult(
            status=report.STATUS_DEGRADED,
            findings=[f"未找到可执行文件：{cmd[0]}（环境缺该工具）"],
            **base,
        )
    if result.timed_out:
        return report.CheckResult(
            status=report.STATUS_FAIL,
            findings=[f"执行超时（>{int(timeout)}s）：{' '.join(str(item) for item in cmd)}"] + tail(result.stdout),
            **base,
        )
    if result.skipped:
        status = report.STATUS_DEGRADED if allowed else report.STATUS_MISSING
        return report.CheckResult(
            status=status,
            findings=["子脚本报告 [skip]（依赖缺失）"] + tail(result.stdout),
            **base,
        )
    if result.ok:
        return report.CheckResult(status=report.STATUS_PASS, evidence=[success_note or "退出码 0"], **base)
    output = result.stderr.strip() or result.stdout
    return report.CheckResult(
        status=report.STATUS_FAIL,
        findings=[f"退出码 {result.returncode}：{' '.join(str(item) for item in cmd)}"] + tail(output),
        **base,
    )
