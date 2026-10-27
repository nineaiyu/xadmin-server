#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""补齐验证码风险规则默认行（升级/修复用，幂等）。"""

from typing import Any

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Ensure built-in captcha risk rules exist (idempotent; never overwrites existing rows)"

    def handle(self, *args: Any, **options: Any) -> None:
        from captcha.models import CaptchaRule
        from captcha.risk import ensure_default_rules

        before = CaptchaRule.objects.count()
        ensure_default_rules()
        after = CaptchaRule.objects.count()
        self.stdout.write(f"captcha risk rules: {before} -> {after}")
