# -*- coding: utf-8 -*-
"""认证 Cookie 读取单一入口守护（SEC-M1 双模式的根因收口）。

背景：HttpOnly 双模式（AUTH_COOKIE_HTTPONLY）落地后，「取认证 Cookie」的位置
分散在内核认证类（X-Token 回退）与 identity 刷新链路（X-Refresh-Token 双读）。
若未来新代码各写一份 ``request.META.get("HTTP_COOKIE")`` + ``parse_cookie``，
解析口径（缺键归一 / 多值 / 编码）就会分叉——双模式演进时个别消费点被漏掉，
正是本轮复核要根除的「机制已建好、个别消费点漏网」形态。

守护两件事：

1. **静态扫描**：全仓非测试代码里 ``parse_cookie`` 与 ``HTTP_COOKIE`` 的直读点
   只允许出现在单一入口 ``common/core/auth/authentication.py:read_auth_cookie``
   （含该模块文件本身）；业务侧（如 identity 的 refresh 双读）必须经入口函数；
2. **入口行为**：缺失归一空串、取值正确——入口自身的语义被钉死。

扫描逻辑与 tests/static-guards（前端）同思路：以正则扫源码文本，新增绕开点即红。
"""

import re
from pathlib import Path
from types import SimpleNamespace

from common.core.auth.authentication import read_auth_cookie

REPO_ROOT = Path(__file__).resolve().parents[3]
# 与 check_task_acks / check_cache_keys 同口径的运行代码扫描面（不含 tests / .venv）
SCAN_DIRS = (
    "packages/xadmin-common/common",
    "system",
    "settings",
    "captcha",
    "ai",
    "dataset",
    "message",
    "identity",
    "file",
    "task",
    "audit",
    "approval",
    "notifications",
    "devtools",
    "mfa",
    "server",
    "integrations",
)
#: parse_cookie / HTTP_COOKIE 唯一允许出现的源码文件（单一入口所在模块）
ENTRY_MODULE = "packages/xadmin-common/common/core/auth/authentication.py"

PARSE_COOKIE_RE = re.compile(r"\bparse_cookie\s*\(")
HTTP_COOKIE_RE = re.compile(r"\bHTTP_COOKIE\b")


def _iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for scan_dir in SCAN_DIRS:
        base = root / scan_dir
        if not base.is_dir():
            continue
        files.extend(sorted(base.rglob("*.py")))
    return [f for f in files if "/migrations/" not in f.as_posix() and not f.name.startswith("test_")]


def scan_direct_cookie_reads(root: Path) -> list[str]:
    """返回绕开单一入口的直读点（``文件:行号`` 清单；空 = 合规）。"""
    offenders: list[str] = []
    for py_file in _iter_source_files(root):
        relative = py_file.relative_to(root).as_posix()
        if relative == ENTRY_MODULE:
            continue
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            if PARSE_COOKIE_RE.search(line) or HTTP_COOKIE_RE.search(line):
                offenders.append(f"{relative}:{lineno}: {line.strip()}")
    return offenders


class TestCookieEntryBehavior:
    """入口函数自身的行为钉死。"""

    @staticmethod
    def _request(cookie_header: str | None) -> SimpleNamespace:
        meta = {} if cookie_header is None else {"HTTP_COOKIE": cookie_header}
        return SimpleNamespace(META=meta)

    def test_reads_named_cookie_value(self):
        request = self._request("X-Token=abc; other=1")
        assert read_auth_cookie(request, "X-Token") == "abc"
        assert read_auth_cookie(request, "other") == "1"

    def test_missing_cookie_header_returns_empty(self):
        assert read_auth_cookie(self._request(None), "X-Token") == ""

    def test_missing_name_returns_empty_not_none(self):
        """缺键归一为空串（调用方按 falsy 判定，None 会破坏类型契约）。"""
        assert read_auth_cookie(self._request("X-Token=abc"), "X-Refresh-Token") == ""

    def test_valueless_cookie_returns_empty(self):
        assert read_auth_cookie(self._request("X-Token="), "X-Token") == ""


class TestCookieEntrySingleAccess:
    """静态守护：认证 Cookie 的解析只允许出现在单一入口模块。"""

    def test_no_direct_cookie_parsing_outside_entry(self):
        """全仓非测试代码不得出现 parse_cookie / HTTP_COOKIE 直读（绕开即红）。

        新消费点应 ``from common.core.auth.authentication import read_auth_cookie``；
        若确需非认证场景读 Cookie（如新中间件），必须评估双模式影响并把豁免
        登记到本测试（写明理由），不允许静默新增直读点。
        """
        offenders = scan_direct_cookie_reads(REPO_ROOT)
        assert offenders == [], "发现绕开 read_auth_cookie 单一入口的 Cookie 直读点:\n" + "\n".join(offenders)

    def test_scanner_detects_bypass_in_sample_tree(self, tmp_path):
        """自测：造违规样例确认扫描器能拦（不拦 = 守护形同虚设）。"""
        (tmp_path / "identity").mkdir()
        (tmp_path / "identity" / "views.py").write_text(
            "from django.http.cookie import parse_cookie\n"
            "def leak(request):\n"
            "    return parse_cookie(request.META.get('HTTP_COOKIE'))\n",
            encoding="utf-8",
        )
        offenders = scan_direct_cookie_reads(tmp_path)
        # import 行的 parse_cookie 无调用括号不匹配；读取行同时命中两个模式，按行报 1 条
        assert len(offenders) == 1
        assert offenders[0].startswith("identity/views.py:3:")
        assert "parse_cookie" in offenders[0] and "HTTP_COOKIE" in offenders[0]

    def test_scanner_allows_entry_module_and_clean_tree(self, tmp_path):
        """自测：入口模块本身与干净树不误报。"""
        entry = tmp_path / "packages/xadmin-common/common/core/auth/authentication.py"
        entry.parent.mkdir(parents=True)
        entry.write_text("cookies = request.META.get('HTTP_COOKIE')\nparse_cookie(cookies)\n", encoding="utf-8")
        assert scan_direct_cookie_reads(tmp_path) == []
