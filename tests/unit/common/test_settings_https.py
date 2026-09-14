#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""S3：HTTPS 部署安全头默认关闭守护。

背景：SECURE_HSTS*/Secure Cookie 一旦默认开启，未配 TLS 的 HTTP 直连部署会
出现 Cookie 丢失、甚至跳转循环（内置 nginx 是纯 TCP stream 代理，Django 拿不到
X-Forwarded-Proto: https）。因此默认必须全部关闭，由部署方在 TLS 终止后通过
``SECURITY_HTTPS_ENABLED`` 显式开启。
"""

from django.conf import settings

from server.const import CONFIG


def test_https_security_headers_default_off():
    """默认（未配 TLS）不得下发 HSTS / Secure Cookie / 强制跳转。"""
    assert settings.SECURITY_HTTPS_ENABLED is False
    assert settings.SECURE_HSTS_SECONDS == 0
    assert settings.SECURE_HSTS_INCLUDE_SUBDOMAINS is False
    assert settings.SECURE_HSTS_PRELOAD is False
    assert settings.SESSION_COOKIE_SECURE is False
    assert settings.CSRF_COOKIE_SECURE is False
    assert settings.SECURE_SSL_REDIRECT is False


def test_nosniff_stays_enabled():
    """X-Content-Type-Options: nosniff 依赖 Django 默认值，不得被 HTTPS 开关误关。"""
    assert settings.SECURE_CONTENT_TYPE_NOSNIFF is True


def test_https_redirect_is_independent_switch():
    """跳转开关独立于安全头开关（TCP 代理下不能借安全头开关顺带开启跳转）。"""
    assert CONFIG.SECURITY_HTTPS_REDIRECT_ENABLED is False
    # 两个键都在 conf.py 默认值中登记（config.yml 未配置时也有兜底）
    assert CONFIG.SECURITY_HTTPS_ENABLED is False
