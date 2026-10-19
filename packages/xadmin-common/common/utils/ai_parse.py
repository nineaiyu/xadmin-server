#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 输出解析公共件：robust JSON 对象提取（跨入口唯一实现）。

背景：动作草稿（``ai_actions``）与 NL 查数（``nl_query``）各自实现过一份
「剥 markdown 码栅 + 掐首尾花括号 + json.loads + 非 dict 拒绝」，两处漂移会让
同一模型输出在两个入口得到不同结果。这里收敛为唯一实现；各调用方只负责把
``AiOutputParseError`` 转成自己框架的错误类型（Django / DRF ValidationError）
与各自的后续处理（如 NL 的未知键剥离）。

位置（common/utils）：无状态、零模型依赖的纯解析件，且拆分后 AI 域与
数据分析域（nl_query）两个业务 app 都要消费——放框架层避免兄弟 app 互引。
"""

import json
import re
from typing import Any

#: markdown 码栅包裹的 JSON（```json {...} ``` / ``` {...} ```）
_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


class AiOutputParseError(ValueError):
    """LLM 输出无法解析为 JSON 对象（调用方转各自框架的错误类型）。"""


def extract_json_object(text: str) -> dict[str, Any]:
    """从 LLM 输出中提取 JSON 对象（robust：容忍码栅、前后废话与多余花括号）。

    策略：优先取 markdown 码栅内的内容；再从每个 ``{`` 起点尝试
    ``JSONDecoder.raw_decode``（解析到对象结束即止，忽略尾随内容）。
    这样既能跳过前后废话，也能容忍模型多包一层花括号
    （实测思考型模型会输出 ``{{"message": "..."}}``）——旧实现取
    「首个 { 到最后一个 }」在双层括号下必然 JSONDecodeError。
    解析失败或结果不是对象时抛 ``AiOutputParseError``。
    """
    text = (text or "").strip()
    fenced = _FENCED.search(text)
    if fenced:
        text = fenced.group(1)
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise AiOutputParseError("the model returned malformed JSON")
