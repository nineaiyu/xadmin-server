#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""覆盖率门禁口径守护（.coveragerc ↔ CI workflow）。

本地 `pytest -n auto --cov` 的阈值由 ``.coveragerc`` 的 ``fail_under`` 生效，
CI 由命令行 ``--cov-fail-under`` 生效（该处同时是文档事实源 ``workflow:cov_fail_under``）。
两处数值漂移会造成"本地绿 / CI 红"（或反之）的假信号，故在此钉住相等。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COVERAGERC = REPO_ROOT / ".coveragerc"
CI_WORKFLOW = REPO_ROOT / ".github/workflows/test.yml"

_FAIL_UNDER_RE = re.compile(r"^\s*fail_under\s*=\s*(\d+)\s*$", re.MULTILINE)
_CI_FLAG_RE = re.compile(r"--cov-fail-under=(\d+)")


def _read_thresholds():
    local = _FAIL_UNDER_RE.search(COVERAGERC.read_text(encoding="utf-8"))
    ci = _CI_FLAG_RE.search(CI_WORKFLOW.read_text(encoding="utf-8"))
    return local, ci


def test_local_and_ci_coverage_thresholds_match():
    local, ci = _read_thresholds()
    assert local is not None, ".coveragerc [report] 缺少 fail_under（本地覆盖率门禁失效）"
    assert ci is not None, "test.yml 缺少 --cov-fail-under（CI 覆盖率门禁与文档事实源失效）"
    assert local.group(1) == ci.group(1)
