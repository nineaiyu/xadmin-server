# -*- coding: utf-8 -*-
"""点选题字体启动检查：缺失必须 fail-fast，而不是首次出题才报错。"""

from django.apps import apps
from django.core.checks import run_checks

from captcha.checks import click_font_exists


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
