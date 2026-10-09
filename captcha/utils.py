#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : util
# author : ly_13
# date : 8/10/2024

from typing import Any

from django.http import HttpRequest
from django.utils import timezone
from rest_framework.request import Request

from captcha.helpers import captcha_image_url
from captcha.models import CaptchaStore
from common.utils import get_logger

logger = get_logger(__name__)


class CaptchaAuth:
    def __init__(self, captcha_key: str = "", request: HttpRequest | Request | None = None) -> None:
        self.captcha_key = captcha_key
        self.request = request

    def __get_captcha_obj(self) -> CaptchaStore | None:
        captcha_obj: CaptchaStore | None = CaptchaStore.objects.filter(hashkey=self.captcha_key).first()
        return captcha_obj

    def generate(self) -> dict[str, Any]:
        self.captcha_key = CaptchaStore.generate_key()
        captcha_image = captcha_image_url(self.captcha_key)
        if self.request:
            captcha_image = self.request.build_absolute_uri(captcha_image)
        captcha_obj = self.__get_captcha_obj()
        code_length = 0
        if captcha_obj:
            code_length = len(captcha_obj.response)
        return {"captcha_image": captcha_image, "captcha_key": self.captcha_key, "length": code_length}

    def valid(self, verify_code: str) -> bool:
        """校验并消费一次验证码（一次性语义）。

        `get() + delete()` 非原子：并发同码可双双通过。改为按删除行数判定——
        单条 SQL 内「匹配到即删除」，仅一个并发请求能拿到 1 行（抢到的通过，其余失败）。
        """
        result = CaptchaStore.objects.filter(
            response=verify_code.strip(" ").lower(), hashkey=self.captcha_key, expiration__gt=timezone.now()
        ).delete()
        deleted: int = result[0]
        return deleted > 0
