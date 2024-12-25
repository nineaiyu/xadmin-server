#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : verify_code
# author : ly_13
# date : 8/6/2024
import time

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from common.sdk.sms.endpoint import SMS
from common.sdk.sms.exceptions import CodeError, CodeExpired, CodeSendOverRate
from common.tasks import send_mail_async
from common.utils import get_logger, random_string

logger = get_logger(__name__)


@shared_task(verbose_name=_('Send SMS code'))
def send_sms_async(target, code):
    SMS().send_verify_code(target, code)


class SendAndVerifyCodeUtil(object):
    KEY_TMPL = 'auth_verify_code_{}'
    RATE_KEY_TMPL = 'auth_verify_code_send_at_{}'

    def __init__(self, target, code=None, key=None, backend='email', timeout=None, limit=None, dryrun=False, **kwargs):
        self.code = code
        self.target = target
        self.backend = backend
        self.dryrun = dryrun
        self.key = key or self.KEY_TMPL.format(target)
        self.timeout = settings.VERIFY_CODE_TTL if timeout is None else timeout
        self.limit = settings.VERIFY_CODE_LIMIT if limit is None else limit
        self.limit_key = self.RATE_KEY_TMPL.format(target)
        self.other_args = kwargs

    def gen_and_send_async(self):
        self.__rata()
        return self.gen_and_send()

    def gen_and_send(self):
        try:
            if not self.code:
                self.__generate()
            self.__send()
        except Exception as e:
            self.__clear()
            raise

    def verify(self, code):
        right = cache.get(self.key)
        if not right:
            raise CodeExpired

        if right != code:
            raise CodeError

        self.__clear()
        return True

    def __clear(self):
        cache.delete(self.key)
        cache.delete(self.limit_key)

    def __ttl(self):
        return cache.ttl(self.key)

    def __rata(self):
        token_send_at = cache.get(self.limit_key, 0)
        if token_send_at:
            raise CodeSendOverRate(cache.ttl(self.limit_key))

    def __get_code(self):
        return cache.get(self.key)

    def __generate(self):
        code = random_string(settings.VERIFY_CODE_LENGTH, lower=settings.VERIFY_CODE_LOWER_CASE,
                             upper=settings.VERIFY_CODE_UPPER_CASE, digit=settings.VERIFY_CODE_DIGIT_CASE)
        self.code = code
        return code

    def __send_with_sms(self):
        send_sms_async.apply_async(args=(self.target, self.code), priority=100)

    def __send_with_email(self):
        subject = self.other_args.get('subject', '')
        message = self.other_args.get('message', '')
        send_mail_async.apply_async(
            args=(subject, message, [self.target]),
            kwargs={'html_message': message}, priority=100
        )

    def __send(self):
        """
        发送信息的方法，如果有错误直接抛出 api 异常
        """
        if not self.dryrun:
            if self.backend == 'sms':
                self.__send_with_sms()
            else:
                self.__send_with_email()

        cache.set(self.key, self.code, self.timeout)
        cache.set(self.limit_key, self.code, self.limit)
        logger.debug(f'Send verify code to {self.target}')


class TokenTempCache(object):
    CACHE_KEY_TOKEN_TEMP_PREFIX = "_KEY_TOKEN_TEMP_CACHE_{}"

    @classmethod
    def generate_cache_token(cls, timeout=3600, data=None):
        token = random_string(50)
        key = cls.CACHE_KEY_TOKEN_TEMP_PREFIX.format(token)
        cache.set(key, {"time": time.time(), 'data': data}, timeout)
        return token

    @classmethod
    def validate_cache_token(cls, token):
        if not token:
            return None
        key = cls.CACHE_KEY_TOKEN_TEMP_PREFIX.format(token)
        value = cache.get(key)
        if not value:
            return None
        try:
            return value.get('data', None)
        except Exception as e:
            logger.error(e, exc_info=True)
            return None

    @classmethod
    def expired_cache_token(cls, token):
        key = cls.CACHE_KEY_TOKEN_TEMP_PREFIX.format(token)
        cache.delete(key)
