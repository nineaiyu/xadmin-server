#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模型能力画像与探测：上线前探明模型能力，把「换模型就静默失败」变「探测即知」。

四项能力（结果落 ``AiProfile.capabilities``，可按需手工修正覆盖）：

- ``json``：要求按给定 schema 输出 JSON 并校验可解析（NL 查数 / 动作草稿的准入判据）；
- ``tool_calls``：携带一个内省 tools 定义，期望返回 ``tool_calls``（双轨的准入判据）；
- ``reasoning``：观察 ``reasoning_content``（思考型模型画像；无思考内容不算调用失败）；
- ``vision``：按需（显式请求）验证多模态输入被供应商接受。

探测不阻断：单项失败只记录 ``ok=False`` + 可读原因，由前端展示与人工判断；
探测结果供链路选择（结构化链路要求 JSON 能力、原生工具调用要求 tool_calls 能力）。
"""

from django.utils import timezone

from common.sdk.ai.chat import AiSdkError, ChatCompletionsClient
from common.utils import get_logger

logger = get_logger(__name__)

CAPABILITY_JSON = "json"
CAPABILITY_TOOL_CALLS = "tool_calls"
CAPABILITY_REASONING = "reasoning"
CAPABILITY_VISION = "vision"
DEFAULT_CAPABILITIES = (CAPABILITY_JSON, CAPABILITY_TOOL_CALLS, CAPABILITY_REASONING)
ALL_CAPABILITIES = DEFAULT_CAPABILITIES + (CAPABILITY_VISION,)

# 探测输出上限：思考型模型需要预算，但探测本身要低成本（单次 <1K token）
PROBE_MAX_TOKENS = 512

JSON_PROBE_PROMPT = (
    'This is a capability probe. Reply with a single JSON object and nothing else: {"status": "ok", "score": 7}'
)
TOOL_PROBE_PROMPT = "Call the xadmin_probe_echo tool with value 'xadmin'. Do not answer with plain text."
TOOL_PROBE_DEF = {
    "type": "function",
    "function": {
        "name": "xadmin_probe_echo",
        "description": "Echo the given value back. Used to verify native tool calling support.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string", "description": "Value to echo"}},
            "required": ["value"],
        },
    },
}
REASONING_PROBE_PROMPT = "Think step by step, then answer: what is 17 times 23? Reply with the number only."
VISION_PROBE_PROMPT = "Reply with the colors you see in this image, separated by commas."
# 8x8 四象限色块（红/蓝/绿/白）：探测多模态输入是否被供应商接受，自带素材零外部依赖
VISION_PROBE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAHElEQVR4nGP4z8AAR0jM/wxUlGBoQKD/SICKEgCnbFex5JLCwQAAAABJRU5ErkJggg=="
)


def _entry(ok: bool, detail: str, extra: dict = None) -> dict:
    entry = {"ok": bool(ok), "detail": str(detail)[:300], "at": timezone.now().isoformat()}
    if extra:
        entry.update(extra)
    return entry


def probe_json(client) -> tuple:
    """结构化 JSON 能力：要求输出固定对象并校验可解析。"""
    from system.utils.ai_parse import extract_json_object

    try:
        raw = client.chat([{"role": "user", "content": JSON_PROBE_PROMPT}], temperature=0, max_tokens=PROBE_MAX_TOKENS)
    except AiSdkError as exc:
        return CAPABILITY_JSON, _entry(False, str(exc))
    try:
        payload = extract_json_object(raw)
    except Exception:  # noqa: BLE001 解析失败即不具备稳定的结构化输出能力
        return CAPABILITY_JSON, _entry(False, "The model did not return a parseable JSON object")
    ok = isinstance(payload, dict) and str(payload.get("status") or "").lower() == "ok"
    return CAPABILITY_JSON, _entry(
        ok, "Structured JSON output supported" if ok else "JSON object returned but its shape is unexpected"
    )


def probe_tool_calls(client) -> tuple:
    """原生 function calling 能力（准入判据）：期望返回 tool_calls。"""
    try:
        result = client.chat_tools(
            [{"role": "user", "content": TOOL_PROBE_PROMPT}],
            tools=[TOOL_PROBE_DEF],
            tool_choice="auto",
            temperature=0,
            max_tokens=PROBE_MAX_TOKENS,
        )
    except AiSdkError as exc:
        return CAPABILITY_TOOL_CALLS, _entry(False, str(exc))
    calls = result.get("tool_calls") or []
    ok = bool(calls)
    detail = "Native tool calls supported" if ok else "The model answered without tool_calls"
    return CAPABILITY_TOOL_CALLS, _entry(ok, detail, {"probe_tools": [call["name"] for call in calls][:3]})


def probe_reasoning(client) -> tuple:
    """思考型模型画像：观察 reasoning_content（无思考内容不算探测失败）。"""
    try:
        client.chat(
            [{"role": "user", "content": REASONING_PROBE_PROMPT}],
            temperature=0,
            max_tokens=PROBE_MAX_TOKENS,
        )
    except AiSdkError as exc:
        return CAPABILITY_REASONING, _entry(False, str(exc))
    reasoning = client.last_reasoning
    if reasoning:
        return CAPABILITY_REASONING, _entry(True, "Reasoning content returned", {"reasoning_chars": len(reasoning)})
    return CAPABILITY_REASONING, _entry(False, "No reasoning_content returned (not a reasoning model)")


def probe_vision(client) -> tuple:
    """多模态输入能力（按需）：验证供应商接受 image_url 消息。"""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_PROBE_PROMPT},
                {"type": "image_url", "image_url": {"url": VISION_PROBE_DATA_URL}},
            ],
        }
    ]
    try:
        client.chat(messages, temperature=0, max_tokens=128)
    except AiSdkError as exc:
        return CAPABILITY_VISION, _entry(False, str(exc))
    return CAPABILITY_VISION, _entry(True, "The provider accepted a multimodal message")


PROBES = {
    CAPABILITY_JSON: probe_json,
    CAPABILITY_TOOL_CALLS: probe_tool_calls,
    CAPABILITY_REASONING: probe_reasoning,
    CAPABILITY_VISION: probe_vision,
}


def capability_ok(profile, name: str) -> bool:
    """档案画像中某项能力是否通过（未探测 / 探测失败一律视为不通过，fail-closed）。"""
    data = (getattr(profile, "capabilities", None) or {}).get(str(name))
    return bool(isinstance(data, dict) and data.get("ok"))


def probe_profile(profile, capabilities=None, vision: bool = False) -> dict:
    """按序探测档案能力，返回可直接落 ``AiProfile.capabilities`` 的结果 dict。

    ``capabilities`` 指定探测子集（缺省三项）；``vision=True`` 追加多模态探测。
    """
    from system.utils.ai_config import profile_credentials

    client = ChatCompletionsClient(profile_credentials(profile))
    names = [str(name) for name in (capabilities or DEFAULT_CAPABILITIES)]
    if vision and CAPABILITY_VISION not in names:
        names.append(CAPABILITY_VISION)
    names = [name for name in names if name in PROBES]
    if not names:
        names = list(DEFAULT_CAPABILITIES)
    result = {}
    for name in names:
        key, entry = PROBES[name](client)
        result[key] = entry
    result["model"] = str(profile.model or "")
    result["probed_at"] = timezone.now().isoformat()
    logger.info(
        "ai profile probed: %s -> %s",
        profile.name,
        {key: entry["ok"] for key, entry in result.items() if isinstance(entry, dict)},
    )
    return result
