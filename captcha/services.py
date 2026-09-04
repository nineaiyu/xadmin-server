#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
captcha app 对外服务契约层。

其他 app 需要使用 captcha 的验证码能力时，只允许从本模块导入，
禁止直接 import captcha.utils 等内部实现，避免 app 间横向依赖扩散。
"""
from captcha.utils import CaptchaAuth

__all__ = ["CaptchaAuth"]
