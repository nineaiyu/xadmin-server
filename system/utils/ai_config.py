#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 配置与凭据收口：激活档案优先，Setting 通路回落（自 ai.py 拆出，仅行数门禁）。

调用面保持 `system.utils.ai` 再导出不变；本模块不依赖 ai.py，无循环导入。
"""

from django.conf import settings

from common.utils import get_logger

logger = get_logger(__name__)

# 聊天室助手内置人设（档案/Setting 未配置人设时的兜底）
BUILTIN_PERSONA = (
    "You are the xadmin in-app assistant. Answer concisely and accurately in the user's language. "
    "If you are unsure, say so instead of making things up."
)
# 结构化输出（NL 查数 DSL / 动作草稿 JSON）在档案未配置 max_tokens 时的安全上限：
# 思考型模型（含本地小模型）在 JSON 指令任务上可能无界推理（实测单次可产出 5 万+
# reasoning token、挂起数分钟），结构化结果本身短，给上限防挂起与额度失控；
# 档案显式配置了 max_tokens 时尊重用户配置，不覆盖。
STRUCTURED_MAX_TOKENS = 2048
# 档案用途（与 AiProfile.Purpose 同口径）：chat 供问答/聊天，structured 供 NL 查数/动作草稿
PURPOSE_CHAT = "chat"
PURPOSE_STRUCTURED = "structured"


def ai_structured_max_tokens() -> int:
    """结构化输出 token 上限（可配置）：Setting ``AI_STRUCTURED_MAX_TOKENS`` →
    内置默认 2048。思考型模型思考消耗大，可在 AI 配置页调大预算（0/缺省 = 内置默认）。"""
    return int(getattr(settings, "AI_STRUCTURED_MAX_TOKENS", 0) or 0) or STRUCTURED_MAX_TOKENS


def structured_chat_client():
    """结构化输出（动作草稿 JSON / NL 查数 DSL）的统一客户端：返回 ``(client, max_tokens)``。

    四条链路共用同一口径（聊天室 ``/do``、助手页 ``action/interpret/stream``、
    ``nl-query/interpret`` 与其流式版）：max_tokens 未配置时套用结构化安全上限，
    避免思考型模型无界推理挂起（实测见 ADR-049）；凭据按 ``structured`` 用途取档案
    （未配该用途档案时回落 chat 激活档案，单档案场景零变化）。
    """
    from common.sdk.ai.chat import ChatCompletionsClient

    client = ChatCompletionsClient(ai_credentials(PURPOSE_STRUCTURED))
    return client, client.max_tokens or ai_structured_max_tokens()


def profile_for(purpose: str = PURPOSE_CHAT):
    """按用途取激活档案：本用途优先 → chat 激活档案 → 任意激活档案（无则 None）。

    单档案场景（默认 ``purpose=chat``）行为与拆分前完全一致；
    「问答用 A 档案、结构化链路用 B 档案」时两行可同时激活（见模型层用途级唯一约束）。
    """
    from system.models.ai import AiProfile

    row = AiProfile.objects.filter(is_active=True, purpose=str(purpose or PURPOSE_CHAT)).first()
    if row is not None:
        return row
    if str(purpose) != PURPOSE_CHAT:
        fallback = AiProfile.objects.filter(is_active=True, purpose=PURPOSE_CHAT).first()
        if fallback is not None:
            return fallback
    return AiProfile.objects.filter(is_active=True).first()


def active_profile():
    """当前激活的 AI 配置档案（问答用途优先；至多每种用途一个激活行）。"""
    return profile_for(PURPOSE_CHAT)


def set_active_profile(profile, active: bool = True) -> None:
    """激活/停用档案：激活时事务内清掉「同用途」其余激活行（用途级部分唯一索引兜底）。"""
    from django.db import transaction

    from system.models.ai import AiProfile

    with transaction.atomic():
        if active:
            peers = AiProfile.objects.exclude(pk=profile.pk).filter(is_active=True)
            peers.filter(purpose=profile.purpose).update(is_active=False)
        if profile.is_active != active:
            profile.is_active = active
            profile.save(update_fields=["is_active", "updated_time"])


def profile_credentials(profile) -> dict:
    """档案行 → SDK credentials dict（api_key 解密；stop 逗号分隔转列表）。"""
    return {
        "base_url": profile.base_url,
        "api_key": profile.api_key_plain,
        "model": profile.model,
        "timeout": profile.timeout,
        "max_retries": profile.max_retries,
        "temperature": profile.temperature,
        "max_tokens": profile.max_tokens,
        "top_p": profile.top_p,
        "frequency_penalty": profile.frequency_penalty,
        "presence_penalty": profile.presence_penalty,
        "seed": profile.seed,
        "stop": profile.stop_list,
        "context_limit": profile.context_limit,
        "persona": (profile.persona or "").strip(),
    }


def _setting_credentials() -> dict:
    """Setting 回落通路（无激活档案时）：新参数键 getattr 兜底（测试/旧库无该键不炸）。"""
    return {
        "base_url": settings.AI_BASE_URL,
        "api_key": settings.AI_API_KEY,
        "model": settings.AI_MODEL,
        "timeout": settings.AI_TIMEOUT,
        "max_retries": getattr(settings, "AI_MAX_RETRIES", 0) or 0,
        "temperature": getattr(settings, "AI_TEMPERATURE", None),
        "max_tokens": getattr(settings, "AI_MAX_TOKENS", 0) or None,
        "top_p": getattr(settings, "AI_TOP_P", None),
        "frequency_penalty": getattr(settings, "AI_FREQUENCY_PENALTY", None),
        "presence_penalty": getattr(settings, "AI_PRESENCE_PENALTY", None),
        "seed": getattr(settings, "AI_SEED", None),
        "stop": getattr(settings, "AI_STOP", "") or "",
        "context_limit": getattr(settings, "AI_CONTEXT_LIMIT", 20) or 20,
        "persona": (getattr(settings, "AI_PERSONA", "") or "").strip(),
    }


def is_configured() -> bool:
    profile = active_profile()
    if profile is not None:
        return profile.is_configured
    return bool(settings.AI_BASE_URL and settings.AI_API_KEY and settings.AI_MODEL)


def is_enabled() -> bool:
    return bool(settings.AI_ASSISTANT_ENABLED) and is_configured()


def ai_credentials(purpose: str = PURPOSE_CHAT) -> dict:
    """SDK 凭据 + 采样参数全集：按用途取激活档案优先，无档案回落 Setting 通路。"""
    profile = profile_for(purpose)
    if profile is not None:
        return profile_credentials(profile)
    return _setting_credentials()


def ai_context_limit() -> int:
    """聊天室多轮上下文条数：档案 → Setting → 内置默认 20。"""
    profile = active_profile()
    if profile is not None and profile.context_limit:
        return profile.context_limit
    return getattr(settings, "AI_CONTEXT_LIMIT", 20) or 20


def ai_persona() -> str:
    """聊天室助手人设：档案 → Setting → 内置默认。"""
    profile = active_profile()
    if profile is not None and (profile.persona or "").strip():
        return profile.persona.strip()
    return (getattr(settings, "AI_PERSONA", "") or "").strip() or BUILTIN_PERSONA


def native_tools_enabled() -> bool:
    """双轨准入：灰度开关开启 **且** 结构化链路档案的 tool_calls 能力探测通过。

    开关（``AI_NATIVE_TOOLS_ENABLED``）缺省关闭；能力画像来自探测且可人工修正。
    未探测 / 探测失败 / Setting 通路（无画像）一律回落 prompt-JSON 轨道（fail-safe）。
    """
    if not bool(getattr(settings, "AI_NATIVE_TOOLS_ENABLED", False)):
        return False
    from system.utils.ai_probe import capability_ok

    profile = profile_for(PURPOSE_STRUCTURED)
    if profile is None:
        return False
    return capability_ok(profile, "tool_calls")


def active_profile_name(purpose: str = PURPOSE_CHAT) -> str:
    """当前用途激活档案名（用量账本归因；Setting 通路返回空串）。"""
    profile = profile_for(purpose)
    return str(getattr(profile, "name", "") or "")
