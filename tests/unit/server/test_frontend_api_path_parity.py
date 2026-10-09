# -*- coding: utf-8 -*-
"""跨端 API 路径一致性守护：前端引用的每个 ``/api/...`` 路径都必须能被后端路由解析。

背景（真实缺陷）：安全日志页请求 ``/api/system/user/log``，后端当时把 identity
（``user/<pk>``）与 audit（``user/log``）同挂 ``/api/system/`` 前缀——路径被接口
遮蔽后 404/错路由，页面静默空列表而无人察觉：前端的路径字面量、后端路由面、
端到端可用性三者之间没有任何自动校验。本测试把「前端源码里出现的 API 路径」与
「后端 URLConf」直接对账（模板变量归一为占位值），路径漂移（改前缀、改名、删端点）
在 CI 直接红灯。

边界：
- 只扫描业务源码（``src/``），排除测试文件（``*.spec.ts`` / ``__tests__`` / ``e2e`` /
  ``mock``）——测试夹具里的假路径不参与对账；
- 跨仓依赖：单仓检出（CI 只拉 xadmin-server）自动 skip，与 CSP / 模块移除等
  既有跨仓守护同口径。
"""

import re
from pathlib import Path

import pytest
from django.urls import Resolver404, resolve

from tests.url_routes import route_pattern_sources

CLIENT_DIR = Path(__file__).resolve().parents[3].parent / "xadmin-client"
CLIENT_SRC = CLIENT_DIR / "src"

pytestmark = pytest.mark.skipif(
    not CLIENT_SRC.exists(),
    reason="单仓检出（无 xadmin-client 源码），跳过跨端 API 路径一致性校验",
)

#: 前端源码里的 API 路径字面量（含模板串；``${...}`` 由模板变量归一处理）
PATH_PATTERN = re.compile(r"""["'`](/api/[A-Za-z0-9_\-/$.{}\\]+)["'`]""")
TEMPLATE_VAR = re.compile(r"\$\{[^}]*\}")

SKIP_PARTS = ("/__tests__/", "/mock/", "/node_modules/", "/coverage/")


def _iter_source_files():
    for suffix in ("*.ts", "*.vue"):
        for path in CLIENT_SRC.rglob(suffix):
            posix = path.as_posix()
            if any(part in posix for part in SKIP_PARTS):
                continue
            if path.name.endswith(".spec.ts") or path.name.endswith(".d.ts"):
                continue
            yield path


def _extract_paths():
    """产出 ``(相对文件, 归一后的 API 路径)``；模板变量归一为占位值。"""
    seen = []
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        for match in PATH_PATTERN.finditer(text):
            raw = match.group(1)
            if raw.endswith("/"):  # 尾斜杠仅为拼接前缀，归一后处理
                raw = raw.rstrip("/")
            # 模板变量（${pk} 等）归一为占位值；``${this.baseApi}`` 形态不会以 /api 开头
            normalized = TEMPLATE_VAR.sub("1", raw)
            normalized = normalized.split("?")[0]
            seen.append((path.relative_to(CLIENT_DIR).as_posix(), normalized))
    return seen


def _path_reachable(api_path: str) -> bool:
    """路径可达判定：精确 resolve 命中，或存在以该路径开头的路由（前缀式拼接引用）。"""
    segments = api_path.strip("/").split("/")
    for cut in range(len(segments), 0, -1):
        candidate = "/" + "/".join(segments[:cut])
        try:
            resolve(candidate)
        except Resolver404:
            continue
        return True
    prefix = api_path.lstrip("/")
    return any(source.startswith(prefix) for source in route_pattern_sources())


class TestFrontendApiPathParity:
    def test_frontend_paths_are_resolvable(self):
        """前端引用的 API 路径必须全部能命中后端路由（否则线上 404 静默失效）。"""
        unresolved = []
        checked = 0
        for source, api_path in _extract_paths():
            checked += 1
            if _path_reachable(api_path):
                continue
            unresolved.append(f"{source}: {api_path}")
        assert checked > 80, f"扫描面异常（仅 {checked} 条路径），检查前端源码目录"
        assert unresolved == [], "以下前端 API 路径在后端路由面不存在：\n  " + "\n  ".join(unresolved)

    def test_known_legacy_paths_absent(self):
        """四域独立前缀后，旧 ``/api/system/<四域>`` 形态不得再出现在前端源码。"""
        legacy = re.compile(
            r"/api/system/(user|dept|role|posts|online|directory|file|logs|mask-rules|exports|imports|"
            r"tasks|webhooks|login|auth|register|refresh|logout|open|api-applications|account-risks|"
            r"login-policies|passkeys|personal-access-tokens|userinfo)"
        )
        offenders = []
        for path in _iter_source_files():
            text = path.read_text(encoding="utf-8")
            for match in legacy.finditer(text):
                offenders.append(f"{path.relative_to(CLIENT_DIR).as_posix()}: {match.group(0)}")
        assert offenders == [], "前端仍引用四域旧前缀：\n  " + "\n  ".join(offenders)
