#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : util
# author : ly_13
# date : 8/10/2024

import logging

from django.utils import timezone

from captcha.helpers import captcha_image_url
from captcha.models import CaptchaStore

logger = logging.getLogger(__name__)


class CaptchaAuth(object):
    def __init__(self, captcha_key=''):
        self.captcha_key = captcha_key

    def __get_captcha_obj(self):
        return CaptchaStore.objects.filter(hashkey=self.captcha_key).first()

    def generate(self):
        self.captcha_key = CaptchaStore.generate_key()
        captcha_image = captcha_image_url(self.captcha_key)
        captcha_obj = self.__get_captcha_obj()
        code_length = 0
        if captcha_obj:
            code_length = len(captcha_obj.response)
        return {"captcha_image": captcha_image, "captcha_key": self.captcha_key, "length": code_length}

    def valid(self, verify_code):
        try:
            CaptchaStore.objects.get(
                response=verify_code.strip(" ").lower(), hashkey=self.captcha_key, expiration__gt=timezone.now()
            ).delete()
        except CaptchaStore.DoesNotExist:
            return False
        return True
