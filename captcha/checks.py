#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""启动期系统检查：点选题字体缺失时 fail-fast。

字体是文字点选题的硬依赖；缺失时若静默降级成英文点选会违背题型语义，
因此在 ``manage.py check`` / 服务启动检查阶段直接报错，而不是等到首次出题。
"""

import os

from django.conf import settings
from django.core.checks import Error, Warning, register


@register()  # type: ignore[untyped-decorator]  # Django 注册器无类型存根
def click_font_exists(app_configs: object, **kwargs: object) -> list[Error]:
    charset = str(getattr(settings, "CAPTCHA_CLICK_CHARSET", "simplified") or "simplified").strip().lower()
    paths = [str(getattr(settings, "CAPTCHA_CLICK_FONT_PATH", "") or "")]
    if charset in {"traditional", "mixed"}:
        paths.append(str(getattr(settings, "CAPTCHA_CLICK_TRADITIONAL_FONT_PATH", "") or ""))
    missing = [path for path in paths if not path or not os.path.isfile(path)]
    if not missing:
        return []
    return [
        Error(
            "Captcha click font is missing",
            hint=f"Expected font files at {paths}; ship the OFL subsets under captcha/resources/fonts/",
            id="captcha.E001",
        )
    ]


@register()  # type: ignore[untyped-decorator]  # Django 注册器无类型存根
def ticket_secret_configured(app_configs: object, **kwargs: object) -> list[Error]:
    if not bool(getattr(settings, "CAPTCHA_ENABLED", True)) or bool(getattr(settings, "DEBUG", False)):
        return []
    if str(getattr(settings, "CAPTCHA_TICKET_SECRET", "") or "").strip():
        return []
    return [
        Error(
            "CAPTCHA_TICKET_SECRET is required in production",
            hint="Set CAPTCHA_TICKET_SECRET in config.yml; deriving from SECRET_KEY is development-only",
            id="captcha.E002",
        )
    ]


@register()  # type: ignore[untyped-decorator]  # Django 注册器无类型存根
def ticket_audience_configured(app_configs: object, **kwargs: object) -> list[Warning]:
    if not bool(getattr(settings, "CAPTCHA_ENABLED", True)) or bool(getattr(settings, "DEBUG", False)):
        return []
    if str(getattr(settings, "CAPTCHA_TICKET_AUDIENCE", "") or "").strip():
        return []
    site = str(getattr(settings, "SITE_URL", "") or "").strip()
    if site and site != "http://127.0.0.1:8000":
        return []
    return [
        Warning(
            "CAPTCHA_TICKET_AUDIENCE is not configured",
            hint="Set CAPTCHA_TICKET_AUDIENCE (deployment id / site URL) to isolate tickets across deployments",
            id="captcha.W002",
        )
    ]


@register()  # type: ignore[untyped-decorator]  # Django 注册器无类型存根
def image_fallback_requires_pow(app_configs: object, **kwargs: object) -> list[Warning]:
    if not bool(getattr(settings, "CAPTCHA_IMAGE_FALLBACK_ENABLED", True)):
        return []
    if bool(getattr(settings, "CAPTCHA_POW_FOR_IMAGE_FALLBACK", True)):
        return []
    return [
        Warning(
            "Image captcha fallback is enabled without PoW",
            hint="Set CAPTCHA_POW_FOR_IMAGE_FALLBACK=True or disable the image fallback",
            id="captcha.W001",
        )
    ]
