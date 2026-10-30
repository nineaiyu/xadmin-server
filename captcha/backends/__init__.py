#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""交互式验证码题型插件包：导入即完成注册。"""

from captcha.backends.base import ChallengeBackend, all_backends, get_backend, register_backend
from captcha.backends.icon import IconBackend
from captcha.backends.image import ImageBackend
from captcha.backends.pow import PowBackend
from captcha.backends.rotate import RotateBackend
from captcha.backends.slider import SlideBackend
from captcha.backends.wordclick import WordClickBackend
from captcha.backends.wordorder import WordOrderBackend

register_backend(ImageBackend())
register_backend(IconBackend())
register_backend(SlideBackend())
register_backend(RotateBackend())
register_backend(WordClickBackend())
register_backend(PowBackend())
register_backend(WordOrderBackend())

__all__ = ["ChallengeBackend", "all_backends", "get_backend", "register_backend"]
