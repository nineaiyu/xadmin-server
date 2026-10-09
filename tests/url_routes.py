# -*- coding: utf-8 -*-
"""URL 路由解析共用工具（测试面）：静态样板提取、遍历与遮蔽检测。

被 ``tests/unit/server/test_url_route_integrity.py``（路由遮蔽守护）与
``tests/unit/server/test_frontend_api_path_parity.py``（跨端路径对账）共用。
"""

import re
from functools import lru_cache

from django.urls import get_resolver
from django.urls.resolvers import RegexPattern, RoutePattern, URLPattern, URLResolver


def static_sample(pattern: object) -> str | None:
    """pattern 的静态样板路径；含动态段（正则分组 / 路径转换器）时返回 None。"""
    if isinstance(pattern, RoutePattern):
        route = pattern._route
        return None if "<" in route else route
    if isinstance(pattern, RegexPattern):
        body = pattern.regex.pattern
        body = re.sub(r"^\^", "", body)
        body = re.sub(r"\\Z$", "", body)
        body = re.sub(r"\$$", "", body)
        # 去锚点后仍含正则元字符 = 动态 pattern（分组/可选/量词等）
        if re.search(r"[()\[\]\\+*?{}|^$]", body):
            return None
        return body
    return None


def iter_leaves(resolver: object, prefix: str = ""):
    """深度优先遍历 URLConf，产出 ``(静态前缀, leaf pattern)``；动态前缀子树跳过。"""
    for entry in getattr(resolver, "url_patterns", []):
        if isinstance(entry, URLResolver):
            sub = static_sample(entry.pattern)
            if sub is None:
                continue
            yield from iter_leaves(entry, prefix + sub)
        elif isinstance(entry, URLPattern):
            yield prefix, entry


def leaf_matches(prefix: str, leaf: URLPattern, path: str) -> bool:
    if not path.startswith(prefix):
        return False
    rest = path[len(prefix) :]
    pattern = leaf.pattern
    if isinstance(pattern, RegexPattern):
        return pattern.regex.match(rest) is not None
    if isinstance(pattern, RoutePattern):
        return pattern.match(rest) is not None
    return False


def scan_shadowed(leaves) -> list[tuple[str, str]]:
    """返回被更早注册的路由遮蔽的静态路由 ``(url, 遮蔽者)``。"""
    entries = [(prefix, leaf, static_sample(leaf.pattern)) for prefix, leaf in leaves]
    shadowed: list[tuple[str, str]] = []
    for prefix, leaf, sample in entries:
        if sample is None:
            continue
        full = prefix + sample
        for other_prefix, other_leaf, _ in entries:
            if other_leaf is leaf:
                break
            if leaf_matches(other_prefix, other_leaf, full):
                shadowed.append((full, f"{other_prefix}{other_leaf.pattern}"))
                break
    return shadowed


@lru_cache(maxsize=1)
def route_pattern_sources() -> tuple[str, ...]:
    """全部 leaf pattern 的完整字符串形态（静态前缀 + pattern，去前导锚点）。

    用于「前缀式引用」可达判定：前端 api 模块常以资源前缀作 baseApi
    （如 ``/api/system/configs`` 拼接 ``/{key}``），前缀本身不是可解析端点。
    """
    sources = []
    for prefix, leaf in iter_leaves(get_resolver()):
        pattern = leaf.pattern
        if isinstance(pattern, RegexPattern):
            body = pattern.regex.pattern.lstrip("^")
        elif isinstance(pattern, RoutePattern):
            body = pattern._route
        else:
            continue
        sources.append((prefix + body).lstrip("/"))
    return tuple(sources)
