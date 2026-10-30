#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""按保留期清理验证码观测日志（CaptchaLog）。

用法::

    python manage.py captcha_purge_logs            # 按 CAPTCHA_LOG_RETENTION_DAYS（默认 30 天）
    python manage.py captcha_purge_logs --days 7   # 本次指定保留期（0 = 只统计不清理）
"""

from typing import Any

from django.core.management.base import BaseCommand

from captcha.models import CaptchaLog


class Command(BaseCommand):
    help = "Purge captcha telemetry logs beyond the retention window"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--days", type=int, default=None, help="Retention window in days (0 = no purge)")

    def handle(self, *args: Any, **options: Any) -> None:
        days: int | None = options.get("days")
        total = CaptchaLog.objects.count()
        deleted = CaptchaLog.purge_expired_logs(retention_days=days)
        self.stdout.write(f"captcha logs: total={total}, purged={deleted}")
