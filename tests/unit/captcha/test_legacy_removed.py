# -*- coding: utf-8 -*-
"""clean cut 守护：旧图片码模块 / 模型 / 路由不得复活。"""

import importlib

import pytest
from django.urls import Resolver404, resolve

pytestmark = pytest.mark.django_db


def test_legacy_model_and_modules_absent():
    from captcha import models

    assert not hasattr(models, "CaptchaStore")
    for module in ("captcha.views", "captcha.urls", "captcha.utils"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)


def test_legacy_routes_absent():
    for path in (
        "/api/identity/auth/captcha",
        "/api/identity/auth/captcha/inspect",
        "/api/system/captcha/image/abc/",
        "/api/system/captcha/audio/abc.wav",
        "/api/system/captcha/refresh/",
    ):
        with pytest.raises(Resolver404):
            resolve(path)
