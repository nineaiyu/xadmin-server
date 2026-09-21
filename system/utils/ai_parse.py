#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 输出解析公共件：robust JSON 对象提取（跨入口唯一实现）。

背景：动作草稿（``ai_actions``）与 NL 查数（``nl_query``）各自实现过一份
「剥 markdown 码栅 + 掐首尾花括号 + json.loads + 非 dict 拒绝」，两处漂移会让
同一模型输出在两个入口得到不同结果。这里收敛为唯一实现；各调用方只负责把
``AiOutputParseError`` 转成自己框架的错误类型（Django / DRF ValidationError）
与各自的后续处理（如 NL 的未知键剥离）。
"""

import json
import re

#: markdown 码栅包裹的 JSON（```json {...} ``` / ``` {...} ```）
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


class AiOutputParseError(ValueError):
    """LLM 输出无法解析为 JSON 对象（调用方转各自框架的错误类型）。"""


def extract_json_object(text: str) -> dict:
    """从 LLM 输出中提取 JSON 对象（robust：容忍码栅与前后废话）。

    策略：优先取 markdown 码栅内的首个大括号块；否则取全文第一个 ``{`` 到
    最后一个 ``}`` 的区间；解析失败或结果不是对象时抛 ``AiOutputParseError``。
    """
    text = (text or "").strip()
    fenced = _FENCED.search(text)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        text = text[start : end + 1] if (start >= 0 and end > start) else text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AiOutputParseError("the model returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise AiOutputParseError("the model returned malformed JSON")
    return payload
