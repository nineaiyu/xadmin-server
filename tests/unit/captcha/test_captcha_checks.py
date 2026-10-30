# -*- coding: utf-8 -*-
"""点选题字体启动检查：缺失必须 fail-fast，而不是首次出题才报错。"""

from django.apps import apps
from django.core.checks import run_checks

from captcha.checks import (
    click_font_exists,
    image_fallback_requires_pow,
    ticket_audience_configured,
    ticket_secret_configured,
)


def test_font_exists_in_default_settings():
    assert click_font_exists(None) == []


def test_missing_font_reports_error(settings, tmp_path):
    settings.CAPTCHA_CLICK_FONT_PATH = str(tmp_path / "missing.ttf")
    errors = click_font_exists(None)
    assert len(errors) == 1
    assert errors[0].id == "captcha.E001"
    assert "missing.ttf" in errors[0].hint


def test_unset_font_reports_error(settings):
    settings.CAPTCHA_CLICK_FONT_PATH = ""
    errors = click_font_exists(None)
    assert errors and errors[0].id == "captcha.E001"


def test_check_is_registered_at_startup(settings, tmp_path):
    settings.CAPTCHA_CLICK_FONT_PATH = str(tmp_path / "missing.ttf")
    errors = run_checks(app_configs=[apps.get_app_config("captcha")])
    assert any(error.id == "captcha.E001" for error in errors)


def test_ticket_secret_required_in_production(settings):
    settings.CAPTCHA_ENABLED = True
    settings.DEBUG = False
    settings.CAPTCHA_TICKET_SECRET = ""
    errors = ticket_secret_configured(None)
    assert [error.id for error in errors] == ["captcha.E002"]
    settings.CAPTCHA_TICKET_SECRET = "configured"
    assert ticket_secret_configured(None) == []
    settings.DEBUG = True
    settings.CAPTCHA_TICKET_SECRET = ""
    assert ticket_secret_configured(None) == []


def test_ticket_audience_required_when_site_url_is_default(settings):
    settings.CAPTCHA_ENABLED = True
    settings.DEBUG = False
    settings.CAPTCHA_TICKET_AUDIENCE = ""
    settings.SITE_URL = "http://127.0.0.1:8000"
    assert [warning.id for warning in ticket_audience_configured(None)] == ["captcha.W002"]
    settings.CAPTCHA_TICKET_AUDIENCE = "deploy-a"
    assert ticket_audience_configured(None) == []


def test_image_fallback_without_pow_warns(settings):
    settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = True
    settings.CAPTCHA_POW_FOR_IMAGE_FALLBACK = False
    warnings = image_fallback_requires_pow(None)
    assert [warning.id for warning in warnings] == ["captcha.W001"]
    settings.CAPTCHA_POW_FOR_IMAGE_FALLBACK = True
    assert image_fallback_requires_pow(None) == []
