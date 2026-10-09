#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : tasks
# author : ly_13
# date : 9/15/2024
from celery import shared_task

from captcha.models import CaptchaStore
from common.celery.decorator import register_as_period_task


@shared_task  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
@register_as_period_task(crontab="12 2 * * *")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def auto_clean_expired_captcha_job() -> None:
    CaptchaStore.remove_expired()
