#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码题型插件包：导入即完成注册。"""

from captcha.backends.base import ChallengeBackend, all_backends, get_backend, register_backend
from captcha.backends.rotate import RotateBackend
from captcha.backends.slider import SlideBackend
from captcha.backends.wordclick import WordClickBackend

register_backend(SlideBackend())
register_backend(RotateBackend())
register_backend(WordClickBackend())

__all__ = ["ChallengeBackend", "all_backends", "get_backend", "register_backend"]
