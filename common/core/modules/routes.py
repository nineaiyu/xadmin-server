#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""模块路由前缀推导：从 app 的 ``config.py::URLPATTERNS`` 推导路由级拦截前缀。

app 侧模块声明里的 ``routes`` 与真实路由（``{app}/config.py`` 的 ``URLPATTERNS``，
经 ``auto_register_app_url`` 注入）原本是两份事实源：前缀改动后忘记同步，就会出现
「路由已迁移、停用后旧前缀仍可访问 / 新前缀漏拦」的静默漏网。

本模块按 app 已注册的 ``URLPATTERNS`` 推导静态前缀（到第一个动态段为止），
声明侧不再重复书写；显式声明仍然优先（可覆盖推导结果）。

边界：只推导 HTTP 路由。WebSocket 通道（``ws_routes``）保持显式声明——WS 准入是
「声明即拦截」的 fail-closed 语义，不按 ``routing.py`` 自动放宽；内置模块的
``routes`` 也在 ``common/core/modules/registry.py::MODULES`` 显式声明（其路由挂在
``server/urls.py``，没有 app 侧 config.py 可推导）。
"""

import re
from dataclasses import replace
from importlib import import_module

from common.utils import get_logger

logger = get_logger(__name__)

# 静态前缀的截止字符：动态段（`<int:pk>`）、正则分组/字符集/量词、锚点、转义
_CUT_CHARS = frozenset("<([{\\?*+^$|.")

# 生成前缀正则时需转义的字符（真正的正则元字符）。刻意不含 `-`、`~`、`&` 等
# `re.escape` 会一并转义但此处无需转义的字符：推导结果同时被权限点前缀复用
# （`permission_prefixes_of` 会去掉 `^` 与前导 `/` 后当字面前缀），多转义会让
# 权限点 path 匹配落空。
_ESCAPE_PATTERN = re.compile(r"([?*+|^$\\.()\[\]{}])")


def _escape_literal(text: str) -> str:
    """把字面前缀转为可安全编译的正则片段（只转义正则元字符）。"""

    return _ESCAPE_PATTERN.sub(r"\\\1", text)


def _pattern_source(pattern) -> str:
    """URLPATTERNS 条目 → 路由来源串（``path()`` 取原始 route，``re_path()`` 取正则）。

    URL 配置对象分三层：``include()`` 得到 ``URLResolver``、``path()`` / ``re_path()``
    得到 ``URLPattern``，两者内层才是 ``RoutePattern``（原始 route）或 ``RegexPattern``
    （正则串）；逐层下钻取第一个可用来源。
    """

    for _ in range(3):
        if pattern is None:
            return ""
        route = getattr(pattern, "_route", None)
        if isinstance(route, str) and route:
            return route
        source = getattr(pattern, "pattern", None)
        if isinstance(source, str):
            return source
        regex = getattr(getattr(pattern, "regex", None), "pattern", None)
        if isinstance(regex, str):
            return regex
        if source is None or source is pattern:
            return ""
        pattern = source
    return ""


def static_prefix(source: str) -> str:
    """路由来源串 → 静态路径前缀（``/api/demo/`` 形态；无静态部分返回空串）。

    ``path("api/demo/", include(...))`` → ``/api/demo/``；
    ``re_path(r"^api/x/(?P<pk>[0-9]+)/$", ...)`` → ``/api/x/``；
    前缀保留原尾斜杠，避免 ``/api/demo`` 误匹配 ``/api/demolition/``。
    """

    text = (source or "").strip()
    if text.startswith("^"):
        text = text[1:]
    for index, char in enumerate(text):
        if char in _CUT_CHARS:
            text = text[:index]
            break
    prefix = "/" + text.lstrip("/")
    return "" if prefix == "/" else prefix


def derive_route_prefixes(app_label: str) -> tuple:
    """app 的 ``config.py::URLPATTERNS`` → 路由前缀正则元组（``^/api/xxx/`` 形态）。

    无 config.py / 无 URLPATTERNS / 导入异常时返回空元组（调用方保持原声明：
    扩展点故障不应拖垮内核启动，路由级拦截退化为「按显式声明」）。
    """

    if not app_label:
        return ()
    try:
        config = import_module(f"{app_label}.config")
    except ModuleNotFoundError:
        return ()
    except Exception as exc:  # noqa: BLE001 扩展点故障不影响内核启动
        logger.warning("load %s.config failed: %s", app_label, exc)
        return ()

    prefixes = []
    for pattern in getattr(config, "URLPATTERNS", None) or ():
        prefix = static_prefix(_pattern_source(pattern))
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    return tuple(f"^{_escape_literal(prefix)}" for prefix in prefixes)


def _apply_derived_routes(app_label: str, declared: tuple) -> tuple:
    """未声明 ``routes`` 的模块从 ``config.py::URLPATTERNS`` 推导路由前缀。

    - 推导成功且声明未写 ``routes`` → 用推导结果（路由前缀单一事实源）；
    - 声明已写 ``routes`` → 原样保留（显式声明优先），但与推导结果不匹配时告警，
      提示声明与真实路由可能已漂移（改了 config.py 前缀却忘记同步声明）；
    - 推导不到（无 config.py）→ 保持原样，未声明 ``routes`` 时告警说明停用后
      其接口不会被拦截。
    """

    derived = derive_route_prefixes(app_label)
    result = []
    for spec in declared:
        if not derived:
            if not spec.routes:
                logger.warning(
                    "模块 %s 未声明 routes，且 %s/config.py 未提供 URLPATTERNS——停用后其接口不会被拦截",
                    spec.id,
                    app_label,
                )
            result.append(spec)
            continue
        if not spec.routes:
            logger.info("模块 %s 的路由前缀自动推导自 %s/config.py：%s", spec.id, app_label, ", ".join(derived))
            result.append(replace(spec, routes=derived))
            continue
        drifted = tuple(route for route in spec.routes if not route.startswith(derived))
        if drifted:
            logger.warning(
                "模块 %s 声明的路由前缀未被 %s/config.py 覆盖（声明与真实路由可能漂移）：%s",
                spec.id,
                app_label,
                ", ".join(drifted),
            )
        result.append(spec)
    return tuple(result)
