#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 统一工具目录：白名单注册表 → 标准化工具描述（MCP tools/list 等价）。

从 ``ai_actions.py`` 拆出仅因行数门禁（500 行）；目录由注册表推导，二者同源
（注册表是唯一白名单，目录只是对外表达），不存在第二份能力清单。

契约：每条 ``{name, description, inputSchema}``，``inputSchema`` 为 JSON Schema
``object``（``const`` 固定参数不出现——服务端持有、不接受模型提供）。这是系统
对外的统一功能接口：LLM function calling、外部 MCP 客户端、二开脚本共用同一份
目录与执行链路（``execute_action``）；新增能力 = 注册表加一条声明（优先用
``ai_api_actions.api_action`` 声明式复用既有业务接口）。
"""

from typing import Any

from common.utils import get_logger

logger = get_logger(__name__)


def _param_schema(rule: dict[str, Any]) -> dict[str, Any]:
    """动作参数声明 → JSON Schema 片段（MCP inputSchema 的 property 体）。"""
    kind = str(rule.get("type") or "string")
    schema: dict[str, Any] = {}
    if kind in ("user", "role", "pk"):
        schema = {"type": "string"}
    elif kind == "int":
        schema = {"type": "integer"}
    elif kind == "bool":
        schema = {"type": "boolean"}
    elif kind == "menu":
        schema = {"type": "array", "items": {"type": "string"}}
    elif kind == "json":
        schema = {"type": "object"}
    elif kind == "enum":
        schema = {"type": "string", "enum": [str(item) for item in rule.get("values") or []]}
    else:
        schema = {"type": "string"}
    if rule.get("description"):
        schema["description"] = str(rule["description"])
    return schema


def tool_catalog(user: Any, exclude_mcp: bool = False) -> list[Any]:
    """当前用户可用动作的标准化目录（按权限 + 可用性双门过滤后的子集）。

    ``exclude_mcp=True`` 仅输出内置动作：外部 MCP 端点（``ai/views/mcp.py``）的
    tools/list 语义是「本系统作为 MCP server 暴露的内置动作」——外接 MCP 工具
    不得经此再暴露给外部客户端（防递归代理与能力二次扩散）。助手 tools 端点、
    openai_tools 与 prompt 目录默认含外接 MCP 工具（动作面）。
    """
    from ai.utils.ai_actions import ACTION_DFORM_SUBMIT, MCP_ACTION_PREFIX, available_actions, available_forms

    tools = []
    for spec in available_actions(user):
        if exclude_mcp and str(spec.key).startswith(MCP_ACTION_PREFIX):
            continue
        # 外接 MCP 动作：params 即 JSON Schema（同步快照已白名单化 + 有界化），直接
        # 下发原文——经 _param_schema 逐参重映射会丢 enum/嵌套/数值类型等精度。
        # 判据用 input_schema 属性存在性（只有 McpActionSpec 携带），静态路径零变化。
        raw_schema = getattr(spec, "input_schema", None)
        if isinstance(raw_schema, dict) and raw_schema:
            schema = dict(raw_schema)
            schema.setdefault("type", "object")
            schema.setdefault("properties", {})
            tools.append({"name": spec.key, "description": str(spec.description), "inputSchema": schema})
            continue
        properties = {}
        required = []
        for name, rule in spec.params.items():
            if "const" in rule:
                continue
            properties[name] = _param_schema(rule)
            if rule.get("required"):
                required.append(name)
        entry = {
            "name": spec.key,
            "description": str(spec.description),
            "inputSchema": {"type": "object", "properties": properties, "required": required},
        }
        if spec.key == ACTION_DFORM_SUBMIT:
            # 动态表单提交：可用表单目录（含字段）随条目下发，供模型选择 form_id
            entry["forms"] = [{"form_id": str(form.pk), "name": form.name} for form in available_forms(user)]
        tools.append(entry)
    return tools


#: 原生 function calling 轨道附加的摘要参数（不进业务参数，仅用于确认卡片文案）
SUMMARY_PARAM = "_summary"
#: 工具描述上限（部分供应商对 description 长度敏感）
MAX_TOOL_DESCRIPTION = 1024


def openai_tools(user: Any) -> list[Any]:
    """工具目录 → OpenAI ``tools`` 定义（同一份 schema 的第三种消费）。

    与 MCP ``tools/list``、助手页 ``tools`` 完全同源（都由 ``tool_catalog`` 推导），
    不存在第二份能力清单；额外附加 ``_summary`` 可选参数供模型产出确认卡片摘要。
    """
    tools = []
    for entry in tool_catalog(user):
        parameters = dict(entry["inputSchema"])
        properties = dict(parameters.get("properties") or {})
        properties[SUMMARY_PARAM] = {
            "type": "string",
            "description": "One-line summary of this action for the confirmation card",
        }
        parameters["properties"] = properties
        description = str(entry["description"])
        forms = entry.get("forms") or []
        if forms:
            hint = ", ".join(f"{form['name']}({form['form_id']})" for form in forms[:20])
            description = f"{description} Available forms: {hint}"
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": entry["name"],
                    "description": description[:MAX_TOOL_DESCRIPTION],
                    "parameters": parameters,
                },
            }
        )
    return tools
