#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 输出脱敏（自 ai_guard 拆分，行为不变）。

敏感形态（api_key / token / JWT / 密文前缀 / ``password=`` 形态，**对所有人生效**）
+ 既有 DataMaskRule 的形态类规则（手机号 / 身份证 / 银行卡 / 邮箱 / custom 正则；
超管与「脱敏豁免」同口径豁免）。命中即替换为 ``[REDACTED]`` 占位符并计数
（不静默截断）。流式输出走 :class:`StreamMasker`：带 hold-back 的增量脱敏，跨帧
敏感串不泄漏。

结构化 JSON 链路（NL DSL / 动作草稿的模型原文）不做文本脱敏——替换会破坏
JSON 结构；可读摘要与最终落库文本仍走脱敏。

配置（SysConfig 可配，默认开）：``AI_OUTPUT_MASK_ENABLED``。
"""

import re
from typing import Any

from django.core.cache import cache

from common.utils import get_logger

logger = get_logger(__name__)

#: 输出脱敏占位符（语言中立，避免随界面语言漂移）
REDACTED = "[REDACTED]"

#: 敏感形态（形状即秘密，不依赖键名；对所有人生效）
_SHAPE_PATTERNS = (
    ("api_key", re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_\-]{12,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}")),
    ("cipher", re.compile(r"\bv2:[A-Za-z0-9+/=]{24,}")),
    ("cipher", re.compile(r"\bv3:[A-Za-z0-9+/=]{24,}")),
    ("cipher", re.compile(r"\bSalted__[A-Za-z0-9+/=]{16,}")),
)

#: ``password=xxx`` 形态：保留键名、只替换值（正则第 2 组）
_KV_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|access[_-]?key)\b(\s*[:=]\s*)[\"']?([A-Za-z0-9_\-./+=]{8,})[\"']?"
)

#: 规则形态类：DataMaskRule.mask_type → 文本级正则（与同口径，超管豁免）
_RULE_SHAPE_PATTERNS = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "idcard": re.compile(r"(?<!\d)\d{17}[\dXx](?!\w)"),
    "bankcard": re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
}

#: 规则形态缓存（活动规则集合，300s；规则变更走既有信号失效链路，这里是读侧快照）
_RULE_PATTERN_CACHE_KEY = "ai_guard_rule_patterns"
_RULE_PATTERN_CACHE_TTL = 300

#: 未完成敏感串前缀探测（匹配到字符串末尾即视为「可能未完」，扣留到下一帧）。
#: 宽模式（ASCII 密钥字符集 / 数字串）覆盖手机号 / 身份证 / 卡号 / JWT / base64 等
#: 的前缀闭包——只要「不完整敏感串」出现在尾部就会被扣留；中文与空白结尾不扣留，
#: 因此普通文本仍逐帧实时输出。
_PARTIAL_TAIL_PATTERNS = (
    re.compile(r"(?:sk|rk|pk)-[A-Za-z0-9_\-]{0,63}$"),
    re.compile(r"AKIA[0-9A-Z]{0,15}$"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{0,63}$"),
    re.compile(r"AIza[0-9A-Za-z_\-]{0,63}$"),
    re.compile(r"v[23]:[A-Za-z0-9+/=]{0,255}$"),
    re.compile(r"Salted__[A-Za-z0-9+/=]{0,255}$"),
    re.compile(r"[A-Za-z0-9_.+\-/=]{1,63}$"),
    re.compile(r"[A-Za-z0-9_\-./+=]{1,63}@[A-Za-z0-9_\-.]{0,63}$"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|access[_-]?key)\b\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+=]{0,63}$"
    ),
)


def output_mask_enabled() -> bool:
    """输出脱敏开关（默认开）。"""
    from django.conf import settings as dj_settings

    return bool(getattr(dj_settings, "AI_OUTPUT_MASK_ENABLED", True))


def _load_rule_text_patterns() -> list[Any]:
    """活动 DataMaskRule 的文本级形态（内置四类 + custom 正则），异常降级空集。"""
    try:
        from django.apps import apps

        rule_model = apps.get_model("audit", "DataMaskRule")
        rows = list(rule_model.objects.filter(is_active=True).values_list("mask_type", "pattern"))
    except Exception:  # noqa: BLE001 模型缺失/库未就绪时不启用规则脱敏
        return []
    patterns = []
    types = {row[0] for row in rows}
    for mask_type in ("phone", "idcard", "bankcard", "email"):
        if mask_type in types:
            patterns.append(_RULE_SHAPE_PATTERNS[mask_type])
    for mask_type, pattern in rows:
        if mask_type == "custom" and pattern:
            try:
                patterns.append(re.compile(str(pattern)))
            except re.error:
                logger.warning("invalid custom mask pattern skipped: %s", pattern)
    return patterns


def rule_text_patterns() -> list[Any]:
    """规则形态正则（300s 缓存；规则变更由既有信号失效链路在下次窗口生效）。"""
    typed_value: list[Any] = cache.get_or_set(
        _RULE_PATTERN_CACHE_KEY, _load_rule_text_patterns, _RULE_PATTERN_CACHE_TTL
    )
    return typed_value


def _rule_masking_for(user: Any) -> bool:
    """规则形态脱敏是否对当前调用者生效（超管豁免，与 DataMaskRule 语义一致）。"""
    if getattr(user, "is_superuser", False):
        return False
    return bool(rule_text_patterns())


def mask_text(text: str, user: Any = None) -> tuple[Any, ...]:
    """对自由文本做输出脱敏，返回 ``(masked_text, hit_count)``。

    - 敏感形态：始终生效（含超管）；
    - 规则形态（手机号 / 身份证 / 银行卡 / 邮箱 / custom）：非超管且有活动规则时生效。
    """
    if not text or not isinstance(text, str) or not output_mask_enabled():
        return text, 0
    hits = 0
    for _name, pattern in _SHAPE_PATTERNS:
        text, count = pattern.subn(REDACTED, text)
        hits += count
    text, count = _KV_SECRET_PATTERN.subn(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    hits += count
    if _rule_masking_for(user):
        for pattern in rule_text_patterns():
            text, count = pattern.subn(REDACTED, text)
            hits += count
    return text, hits


def _custom_probe_patterns() -> list[Any]:
    """custom 脱敏规则中可用于「未完成探测」的正则（跳过可能无界的模式）。

    ``.*`` / ``.+`` 这类模式会让探测恒命中（全部内容被无限期扣留），直接跳过：
    跳过只影响流式探测的保守程度，不影响最终 flush 时的完整脱敏。
    """
    try:
        from django.apps import apps

        rows = list(
            apps.get_model("audit", "DataMaskRule")
            .objects.filter(is_active=True, mask_type="custom")
            .values_list("pattern", flat=True)
        )
    except Exception:  # noqa: BLE001 模型缺失/库未就绪时不参与探测
        return []
    patterns = []
    for raw in rows:
        text = str(raw or "")
        if not text or len(text) > 200 or ".*" in text or ".+" in text:
            continue
        try:
            patterns.append(re.compile(text))
        except re.error:
            continue
    return patterns


def _partial_prefix_len(text: str, extra_patterns: Any = ()) -> int:
    """返回文本尾部「可能未完成的敏感串前缀」长度（0 = 无）。

    extra_patterns：当前调用者启用的 custom 规则正则（一并进行未完成探测）。
    """
    if not text:
        return 0
    tail = text[-128:]
    for pattern in tuple(_PARTIAL_TAIL_PATTERNS) + tuple(extra_patterns):
        match = pattern.search(tail)
        if match and match.end() == len(tail):
            typed_value: int = len(tail) - match.start()
            return typed_value
    return 0


class StreamMasker:
    """流式输出脱敏器：增量 feed + flush，跨帧敏感串不泄漏。

    策略：**默认即发**（不牺牲 SSE 实时性），只扣留文本尾部「可能未完成的敏感串
    前缀」（``_partial_prefix_len`` 探测，含敏感形态与当前调用者的 custom 规则）；
    敏感串一旦完整出现即被替换为占位符。流结束时 ``flush()`` 冲刷剩余缓冲。
    """

    def __init__(self, user: Any = None) -> None:
        self._user = user
        self._buffer = ""
        self.hits = 0
        self._extra_patterns = _custom_probe_patterns() if _rule_masking_for(user) else []

    @property
    def pending(self) -> str:
        """尚未发送的缓冲（测试与诊断用）。"""
        return self._buffer

    def feed(self, delta: str) -> str:
        """喂入增量，返回可安全发送的脱敏文本（可能为空串）。"""
        if not delta:
            return ""
        if not output_mask_enabled():
            return delta
        self._buffer += delta
        extra = _partial_prefix_len(self._buffer, self._extra_patterns)
        if extra:
            head, tail = self._buffer[:-extra], self._buffer[-extra:]
        else:
            head, tail = self._buffer, ""
        if not head:
            return ""
        masked, hits = mask_text(head, self._user)
        self.hits += hits
        self._buffer = tail
        typed_value: str = masked
        return typed_value

    def flush(self) -> str:
        """冲刷剩余缓冲（流结束时调用）。"""
        buffered, self._buffer = self._buffer, ""
        if not buffered:
            return ""
        if not output_mask_enabled():
            return buffered
        masked, hits = mask_text(buffered, self._user)
        self.hits += hits
        typed_value: str = masked
        return typed_value
