#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""captcha 域对外契约层（跨 app 唯一允许的导入面）。

业务 app 不直接 import ``captcha.serializers`` / ``captcha.backends`` 等内部
模块；视图用到的请求/响应序列化器也在此再导出，保证跨域依赖只有一条缝。
"""

from captcha.const import CAPTCHA_TYPES, captcha_globally_enabled, captcha_scene_enabled
from captcha.serializers import (
    CaptchaChallengeInSerializer,
    CaptchaChallengeOutSerializer,
    CaptchaChallengeResponseSerializer,
    CaptchaVerifyInSerializer,
    CaptchaVerifyOutSerializer,
    CaptchaVerifyResponseSerializer,
)
from captcha.services import (
    CaptchaAuth,
    CaptchaExhaustedError,
    CaptchaInvalidError,
    CaptchaServiceError,
    InteractiveCaptcha,
    build_captcha_meta,
    enabled_captcha_types,
    inspect_challenge,
    preview_image_captcha,
    preview_interactive_challenge,
)

__all__ = [
    "CaptchaAuth",
    "CaptchaChallengeInSerializer",
    "CaptchaChallengeOutSerializer",
    "CaptchaChallengeResponseSerializer",
    "CaptchaExhaustedError",
    "CaptchaInvalidError",
    "CaptchaServiceError",
    "CaptchaVerifyInSerializer",
    "CaptchaVerifyOutSerializer",
    "CaptchaVerifyResponseSerializer",
    "InteractiveCaptcha",
    "build_captcha_meta",
    "CAPTCHA_TYPES",
    "captcha_globally_enabled",
    "captcha_scene_enabled",
    "enabled_captcha_types",
    "inspect_challenge",
    "preview_image_captcha",
    "preview_interactive_challenge",
]
