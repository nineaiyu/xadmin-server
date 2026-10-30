#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码题型插件基座与注册表。

新增题型 = 实现 ``ChallengeBackend`` 子类并登记到 ``_BACKENDS``；生成、校验、
缓存、限流、ticket 由上层 ``captcha.services.InteractiveCaptcha`` 统一编排。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from captcha.const import CAPTCHA_DIFFICULTY_EASY, CAPTCHA_DIFFICULTY_HARD


@dataclass
class CaptchaChallenge:
    """一道题的生成结果：secret 只落服务端缓存，payload 才下发客户端。"""

    type: str
    secret: dict[str, Any]
    payload: dict[str, Any] = field(default_factory=dict)


def difficulty_factor(difficulty: str = "", captcha_type: str = "") -> float:
    """难度对容差的缩放：题型映射优先，其次全局 easy/hard 系数。"""
    mapping = getattr(settings, "CAPTCHA_DIFFICULTY_MAP", None)
    if isinstance(mapping, dict) and captcha_type:
        try:
            value = float((mapping.get(captcha_type) or {}).get(difficulty) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    if difficulty == CAPTCHA_DIFFICULTY_EASY:
        return float(getattr(settings, "CAPTCHA_DIFFICULTY_EASY_FACTOR", 1.5) or 1.5)
    if difficulty == CAPTCHA_DIFFICULTY_HARD:
        return float(getattr(settings, "CAPTCHA_DIFFICULTY_HARD_FACTOR", 0.5) or 0.5)
    return 1.0


class ChallengeBackend(ABC):
    """题型插件契约：generate 出题、verify 校验答案（容差口径由题型自持）。"""

    type: str = ""

    @abstractmethod
    def generate(self, difficulty: str = "normal") -> CaptchaChallenge:
        """生成一道题；答案写入 ``secret``，公开数据写入 ``payload``。"""

    @abstractmethod
    def verify(self, secret: dict[str, Any], answer: Any, difficulty: str = "normal") -> bool:
        """服务端容差校验；任何类型/字段异常均判为不通过。"""


_BACKENDS: dict[str, ChallengeBackend] = {}


def register_backend(backend: ChallengeBackend) -> ChallengeBackend:
    if not backend.type:
        raise ValueError("captcha backend requires a non-empty type")
    _BACKENDS[backend.type] = backend
    return backend


def get_backend(captcha_type: str) -> ChallengeBackend | None:
    return _BACKENDS.get(captcha_type)


def all_backends() -> dict[str, ChallengeBackend]:
    return dict(_BACKENDS)
