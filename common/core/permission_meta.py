#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""子 action 权限口径注册表（权限点元数据化）。

历史问题：``IsAuthenticated._resolve_menu_pk`` 用硬编码 URL 后缀 if 分支维护
「哪些子 action 与父级 list 权限同口径」——每加一个这类 action 都要改权限核心类，
且声明（modelset 装饰器）与消费（permission.py）两处手工同步。

现在：action 声明处直接用本模块的装饰器登记口径，权限核心按注册表消费，
双向零手工同步；二开 ViewSet 的新增子 action 同样只声明、不改核心。

两档语义（与历史行为一一对应）：
- ``shared_list_action``：请求 URL 剥掉该后缀后按父级 list 权限解析
  （无独立权限点概念）。历史硬编码：search-columns / suggestions /
  available-forms / user-options。
- ``parent_fallback_action``：优先按自身权限点解析（角色显式绑定了
  ``api/xxx/import-data$`` 这类路径时生效），未绑定时回退父级 list / create。
  历史硬编码：export|import-(data|async|validate|headers)。
"""

import re

# 后缀登记表（存 url_path，不含斜杠；运行期编译为 $ 锚定正则）
SHARED_LIST_SUFFIXES: set[str] = set()
PARENT_FALLBACK_SUFFIXES: set[str] = set()

# 编译缓存：注册表只在模块导入期（URLconf 装载前）变化，按规模失效即可
_shared_list_cache: tuple[int, re.Pattern | None] = (-1, None)
_parent_fallback_cache: tuple[int, re.Pattern | None] = (-1, None)


def register_shared_list(url_path: str) -> None:
    """登记「与父级 list 权限同口径」的子 action 后缀。"""
    SHARED_LIST_SUFFIXES.add(url_path.strip("/"))


def register_parent_fallback(url_path: str) -> None:
    """登记「自身权限点优先、父级 list/create 兜底」的子 action 后缀。"""
    PARENT_FALLBACK_SUFFIXES.add(url_path.strip("/"))


# 框架默认口径（静态兜底层，与历史硬编码正则完全等价）：
# 注册表由装饰器在视图模块导入时填充——不经过 URL 解析的调用方（单测直调
# has_permission、离线扫描器）读不到装饰器副作用，正则必须始终包含这批历史口径。
# 若框架 action 的 url_path 改名，test_permission_meta 的默认口径守护会失败提醒同步。
FRAMEWORK_SHARED_LIST = frozenset({"search-columns", "suggestions", "available-forms", "user-options"})
FRAMEWORK_PARENT_FALLBACK = frozenset(
    {
        "export-data",
        "export-async",
        "export-validate",
        "export-headers",
        "import-data",
        "import-async",
        "import-validate",
        "import-headers",
    }
)


def _compiled_suffixes(suffixes: frozenset[str], cache: tuple[int, re.Pattern | None]) -> tuple[int, re.Pattern | None]:
    if cache[0] != len(suffixes):
        pattern = re.compile(r"/(?:" + "|".join(sorted(suffixes)) + r")$") if suffixes else None
        cache = (len(suffixes), pattern)
    return cache


def shared_list_pattern() -> re.Pattern | None:
    """shared_list 后缀的 ``/xxx$`` 匹配正则（剥离 URL 尾部后缀用）。"""
    global _shared_list_cache
    effective = FRAMEWORK_SHARED_LIST | SHARED_LIST_SUFFIXES
    _shared_list_cache = _compiled_suffixes(effective, _shared_list_cache)
    return _shared_list_cache[1]


def parent_fallback_pattern() -> re.Pattern | None:
    """parent_fallback 后缀的 ``/xxx$`` 匹配正则。"""
    global _parent_fallback_cache
    effective = FRAMEWORK_PARENT_FALLBACK | PARENT_FALLBACK_SUFFIXES
    _parent_fallback_cache = _compiled_suffixes(effective, _parent_fallback_cache)
    return _parent_fallback_cache[1]


def _registering_action(register, **action_kwargs):
    """DRF ``@action`` 的登记变体：按 url_path（缺省由方法名派生，与 DRF 同规则）登记后转发。"""
    from rest_framework.decorators import action

    def decorator(func):
        url_path = action_kwargs.get("url_path") or func.__name__.replace("_", "-")
        register(url_path)
        return action(**action_kwargs)(func)

    return decorator


def shared_list_action(**action_kwargs):
    """``@shared_list_action(methods=..., detail=False, url_path=...)``：
    声明即登记为「与父级 list 权限同口径」。"""
    return _registering_action(register_shared_list, **action_kwargs)


def parent_fallback_action(**action_kwargs):
    """``@parent_fallback_action(...)``：声明即登记为「自身权限点优先、父级兜底」。"""
    return _registering_action(register_parent_fallback, **action_kwargs)
