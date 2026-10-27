#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码服务异常（独立模块避免 services / 编排辅助循环导入）。"""


class CaptchaServiceError(Exception):
    """交互式验证码业务失败基类；视图按 ``code`` 返回 HTTP 400。"""

    code = "captcha_invalid"


class CaptchaInvalidError(CaptchaServiceError):
    code = "captcha_invalid"


class CaptchaExhaustedError(CaptchaServiceError):
    code = "captcha_exhausted"
