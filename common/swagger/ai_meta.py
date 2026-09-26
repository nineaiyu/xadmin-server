#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""OpenAPI 的 AI 可发现性元数据：为 operation 注入 ``x-ai-*`` 扩展字段。

来源有两层（后者覆盖前者）：

1. **注册表自动派生**：``API_ACTION_SPECS`` 中声明的动作按 (method, path) 匹配到
   operation，注入 ``x-ai-action`` / ``x-ai-guidance`` / ``x-ai-required-permissions``
   / ``x-ai-requires-approval``——同一份注册表三种消费（MCP tools/list、助手页 tools、
   OpenAPI 元数据），不存在第二份能力清单；
2. **视图显式声明**：``view.ai_meta = {"visible": False, "guidance": "..."}``，
   适合「不暴露给 AI / 需要额外说明」的端点。

外部集成（MCP 客户端 / 二开脚本 / 文档站）据此知道「哪些端点可 AI 化、需何权限、
参数怎么填」，无需阅读源码。
"""

import re

from ai.services import API_ACTION_SPECS

#: OpenAPI 扩展字段前缀（与 drf-spectacular 的 x-* 约定一致）
EXTENSION_PREFIX = "x-ai-"

_PATH_PARAM = re.compile(r"\{([^}]+)\}")


def normalize_path(path: str) -> str:
    """OpenAPI 路径 → 声明式动作路径口径（``{pk}`` → ``<pk>``，去尾斜杠）。"""
    normalized = _PATH_PARAM.sub(r"<\1>", str(path or ""))
    return normalized.rstrip("/")


def declared_actions() -> dict:
    """声明式动作索引：``{(METHOD, "/api/.../ <pk>"): spec}``（注册表是唯一来源）。"""
    index = {}
    for spec in API_ACTION_SPECS.values():
        index[(str(spec.method).upper(), normalize_path(spec.path))] = spec
    return index


def _approval_flag(value):
    """审批标记：bool 原样；谓词（按用户判定）标 conditional。"""
    return value if isinstance(value, bool) else "conditional"


def ai_operation_meta(view, path: str, method: str) -> dict:
    """收集 operation 的 AI 元数据（无任何来源时返回空 dict，端点零变化）。"""
    meta: dict = {}
    spec = declared_actions().get((str(method or "").upper(), normalize_path(path)))
    if spec is not None:
        meta.update(
            {
                "action": spec.key,
                "label": str(spec.label),
                "guidance": str(spec.description),
                "required-permissions": [f"{m} {p}" for m, p in spec.required_visits],
                "requires-approval": _approval_flag(spec.requires_approval),
                "params": sorted(name for name, rule in (spec.params or {}).items() if "const" not in rule),
            }
        )
    explicit = getattr(view, "ai_meta", None)
    if isinstance(explicit, dict):
        meta.update({str(key): value for key, value in explicit.items()})
    return meta


def operation_extensions(meta: dict) -> dict:
    return {f"{EXTENSION_PREFIX}{key}": value for key, value in meta.items()}
