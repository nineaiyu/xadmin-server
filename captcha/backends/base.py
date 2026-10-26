#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码题型插件基座与注册表。

新增题型 = 实现 ``ChallengeBackend`` 子类并登记到 ``_BACKENDS``；生成、校验、
缓存、限流、ticket 由上层 ``captcha.services.InteractiveCaptcha`` 统一编排。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CaptchaChallenge:
    """一道题的生成结果：secret 只落服务端缓存，payload 才下发客户端。"""

    type: str
    secret: dict[str, Any]
    payload: dict[str, Any] = field(default_factory=dict)


class ChallengeBackend(ABC):
    """题型插件契约：generate 出题、verify 校验答案（容差口径由题型自持）。"""

    type: str = ""

    @abstractmethod
    def generate(self) -> CaptchaChallenge:
        """生成一道题；答案写入 ``secret``，公开数据写入 ``payload``。"""

    @abstractmethod
    def verify(self, secret: dict[str, Any], answer: Any) -> bool:
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
