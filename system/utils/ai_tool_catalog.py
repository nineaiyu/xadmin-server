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

from common.utils import get_logger

logger = get_logger(__name__)


def _param_schema(rule: dict) -> dict:
    """动作参数声明 → JSON Schema 片段（MCP inputSchema 的 property 体）。"""
    kind = str(rule.get("type") or "string")
    schema: dict = {}
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


def tool_catalog(user) -> list:
    """当前用户可用动作的标准化目录（按权限 + 可用性双门过滤后的子集）。"""
    from system.utils.ai_actions import ACTION_DFORM_SUBMIT, available_actions, available_forms

    tools = []
    for spec in available_actions(user):
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
