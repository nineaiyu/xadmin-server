#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""captcha 域对外契约层（跨 app 唯一允许的导入面）。

业务 app 不直接 import ``captcha.serializers`` / ``captcha.backends`` 等内部
模块；视图用到的请求/响应序列化器也在此再导出，保证跨域依赖只有一条缝。
观测面（日志列表 / 大盘聚合）的模型、过滤与统计入口同样只经此导出。
"""

from captcha.const import CAPTCHA_TYPES, captcha_globally_enabled, captcha_scene_enabled
from captcha.filters import CaptchaLogFilter
from captcha.meta import inspect_challenge
from captcha.models import CaptchaLog, CaptchaRule
from captcha.serializers import (
    CaptchaChallengeInSerializer,
    CaptchaChallengeOutSerializer,
    CaptchaChallengeResponseSerializer,
    CaptchaLogSerializer,
    CaptchaRuleSerializer,
    CaptchaRuleUpdateSerializer,
    CaptchaStatsInSerializer,
    CaptchaStatsResponseSerializer,
    CaptchaStatsSerializer,
    CaptchaVerifyInSerializer,
    CaptchaVerifyOutSerializer,
    CaptchaVerifyResponseSerializer,
)
from captcha.services import (
    CaptchaInvalidError,
    CaptchaServiceError,
    InteractiveCaptcha,
    build_captcha_meta,
    enabled_captcha_types,
    preview_image_captcha,
    preview_interactive_challenge,
)
from captcha.signals import xff_inconsistent
from captcha.stats import captcha_stats

__all__ = [
    "CaptchaChallengeInSerializer",
    "CaptchaChallengeOutSerializer",
    "CaptchaChallengeResponseSerializer",
    "CaptchaInvalidError",
    "CaptchaLog",
    "CaptchaLogFilter",
    "CaptchaRule",
    "CaptchaRuleSerializer",
    "CaptchaRuleUpdateSerializer",
    "CaptchaLogSerializer",
    "CaptchaServiceError",
    "CaptchaStatsInSerializer",
    "CaptchaStatsResponseSerializer",
    "CaptchaStatsSerializer",
    "CaptchaVerifyInSerializer",
    "CaptchaVerifyOutSerializer",
    "CaptchaVerifyResponseSerializer",
    "InteractiveCaptcha",
    "build_captcha_meta",
    "CAPTCHA_TYPES",
    "captcha_globally_enabled",
    "captcha_scene_enabled",
    "captcha_stats",
    "xff_inconsistent",
    "enabled_captcha_types",
    "inspect_challenge",
    "preview_image_captcha",
    "preview_interactive_challenge",
]
