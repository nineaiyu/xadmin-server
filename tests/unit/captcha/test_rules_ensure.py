# -*- coding: utf-8 -*-
"""默认风险规则补齐：缺行补默认、已有行不覆盖。"""

import pytest

from captcha.models import CaptchaRule
from captcha.risk import DEFAULT_RULE_CODES, ensure_default_rules

pytestmark = pytest.mark.django_db


def test_ensure_default_rules_fills_missing_rows():
    CaptchaRule.objects.all().delete()
    ensure_default_rules()
    assert set(CaptchaRule.objects.values_list("code", flat=True)) == set(DEFAULT_RULE_CODES)


def test_ensure_default_rules_keeps_existing_values():
    row = CaptchaRule.objects.get(code="honeypot")
    row.score = 42
    row.save(update_fields=["score"])
    ensure_default_rules()
    row.refresh_from_db()
    assert row.score == 42
