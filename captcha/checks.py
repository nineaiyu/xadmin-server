#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""启动期系统检查：点选题字体缺失时 fail-fast。

字体是文字点选题的硬依赖；缺失时若静默降级成英文点选会违背题型语义，
因此在 ``manage.py check`` / 服务启动检查阶段直接报错，而不是等到首次出题。
"""

import os

from django.conf import settings
from django.core.checks import Error, register


@register()  # type: ignore[untyped-decorator]  # Django 注册器无类型存根
def click_font_exists(app_configs: object, **kwargs: object) -> list[Error]:
    path = str(getattr(settings, "CAPTCHA_CLICK_FONT_PATH", "") or "")
    if path and os.path.isfile(path):
        return []
    return [
        Error(
            "Captcha click font is missing",
            hint=f"Expected font file at {path or '<unset>'}; ship the OFL subset under captcha/resources/fonts/",
            id="captcha.E001",
        )
    ]
