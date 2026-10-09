#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : verify_code
# author : ly_13
# date : 8/6/2024
import secrets
import time
from collections.abc import Callable
from typing import Any

from celery import shared_task
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException

from common.settings_contract import kernel_setting
from common.tasks import send_mail_async
from common.utils import get_logger, random_string

logger = get_logger(__name__)

#: 验证码兜底有效期（秒）与发送限流窗口（秒）：配置缺失（None）时使用——
#: cache.set(..., None) 会让验证码永久有效，cache.add(..., None) 会让发送被永久拒绝
DEFAULT_VERIFY_CODE_TTL = 300
DEFAULT_VERIFY_CODE_LIMIT = 60
#: 单个验证码允许的最大校验失败次数（超过即作废，阻断在线爆破）
MAX_VERIFY_ATTEMPTS = 5


# --- 验证码域异常语义（框架层定义） ---------------------------------------
# 定义在 common：验证码校验与发送限流是框架层编排，异常类型须随编排走，
# 短信供应商适配器侧仅再导出保持导入面（类型同一，非双实现）。
class CodeExpired(APIException):
    default_code = "verify_code_expired"
    default_detail = _("The verification code has expired. Please resend it")


class CodeError(APIException):
    default_code = "verify_code_error"
    default_detail = _("The verification code is incorrect")


class CodeSendTooFrequently(APIException):
    default_code = "code_send_too_frequently"
    default_detail = _("Please wait {} seconds before sending")

    def __init__(self, ttl: Any) -> None:
        super().__init__(detail=self.default_detail.format(ttl))


class CodeSendOverRate(APIException):
    default_code = "code_send_over_rate"
    default_detail = _("Please wait {} seconds before sending")

    def __init__(self, ttl: Any) -> None:
        super().__init__(detail=self.default_detail.format(ttl))


class VerifyCodeSenderNotRegistered(RuntimeError):
    """验证码短信发送实现未注册：外部服务接入域未装配时发送明确失败，不静默丢码。"""


# 验证码短信发送实现：外部服务接入域（短信供应商适配器）在 AppConfig.ready 注入。
# 框架层不直接依赖具体供应商——未注册时发送任务 fail-fast，绝不静默吞掉验证码。
_verify_code_sender: Callable[[str, str], None] | None = None


def register_verify_code_sender(sender: Callable[[str, str], None] | None) -> None:
    """注册验证码短信发送实现 ``sender(target, code)``（传 None 撤销，供测试清理）。"""
    global _verify_code_sender
    _verify_code_sender = sender


@shared_task(verbose_name=_("Send SMS code"))  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def send_sms_async(target: Any, code: Any) -> None:
    sender = _verify_code_sender
    if sender is None:
        raise VerifyCodeSenderNotRegistered(
            "verify code SMS sender is not registered; the integrations app must register one in ready()"
        )
    sender(target, code)


class SendAndVerifyCodeUtil:
    KEY_TMPL = "auth_verify_code_{}"
    RATE_KEY_TMPL = "auth_verify_code_send_at_{}"

    def __init__(
        self,
        target: Any,
        code: Any = None,
        key: Any = None,
        backend: str = "email",
        timeout: Any = None,
        limit: Any = None,
        dryrun: bool = False,
        **kwargs: Any,
    ) -> None:
        self.code = code
        self.target = target
        self.backend = backend
        self.dryrun = dryrun
        self.key = key or self.KEY_TMPL.format(target)
        # 配置缺失（None）时回退兜底常量：None 会让 cache.set 永不过期 / 发送被永久拒绝
        self.timeout = (
            timeout if timeout is not None else (kernel_setting("VERIFY_CODE_TTL") or DEFAULT_VERIFY_CODE_TTL)
        )
        self.limit = limit if limit is not None else (kernel_setting("VERIFY_CODE_LIMIT") or DEFAULT_VERIFY_CODE_LIMIT)
        self.limit_key = self.RATE_KEY_TMPL.format(target)
        self.other_args = kwargs

    def gen_and_send_async(self) -> Any:
        self.__rata()
        return self.gen_and_send()

    def gen_and_send(self) -> None:
        try:
            if not self.code:
                self.__generate()
            self.__send()
        except Exception:
            self.__clear()
            raise

    def verify(self, code: Any) -> bool:
        right = cache.get(self.key)
        if not right:
            raise CodeExpired

        # 常量时间比较（避免按字符提前返回的时序侧信道）；两侧都字符串化
        if not secrets.compare_digest(str(right), str(code)):
            if self._note_failed_attempt() >= MAX_VERIFY_ATTEMPTS:
                # 连续错误达上限：作废当前验证码（不动发送限流键，
                # 否则"输错即重置发送间隔"会变成刷短信的新入口）
                cache.delete(self.key)
                cache.delete(self._failed_key)
            raise CodeError

        self.__clear()
        return True

    @property
    def _failed_key(self) -> str:
        return f"{self.key}_failed"

    def _note_failed_attempt(self) -> int:
        """累计校验失败次数（缓存不可用时按 1 计：计数是加固，不阻断校验语义）。"""
        try:
            if cache.add(self._failed_key, 1, self.timeout):
                return 1
            return int(cache.incr(self._failed_key))
        except Exception:  # noqa: BLE001
            logger.warning("verify code attempt counter unavailable", exc_info=True)
            return 1

    def __clear(self) -> None:
        cache.delete(self.key)
        cache.delete(self.limit_key)
        cache.delete(self._failed_key)

    def __ttl(self) -> Any:
        return cache.ttl(self.key)

    def __rata(self) -> None:
        token_send_at = cache.get(self.limit_key, 0)
        if token_send_at:
            raise CodeSendOverRate(cache.ttl(self.limit_key))

    def __get_code(self) -> Any:
        return cache.get(self.key)

    def __generate(self) -> Any:
        code = random_string(
            kernel_setting("VERIFY_CODE_LENGTH"),
            lower=kernel_setting("VERIFY_CODE_LOWER_CASE"),
            upper=kernel_setting("VERIFY_CODE_UPPER_CASE"),
            digit=kernel_setting("VERIFY_CODE_DIGIT_CASE"),
        )
        self.code = code
        return code

    def __send_with_sms(self) -> None:
        send_sms_async.apply_async(args=(self.target, self.code), priority=100)

    def __send_with_email(self) -> None:
        subject = self.other_args.get("subject", "")
        message = self.other_args.get("message", "")
        send_mail_async.apply_async(
            args=(subject, message, [self.target]), kwargs={"html_message": message}, priority=100
        )

    def __send(self) -> None:
        """
        发送信息的方法，如果有错误直接抛出 api 异常
        """
        if not self.dryrun:
            if self.backend == "sms":
                self.__send_with_sms()
            else:
                self.__send_with_email()

        cache.set(self.key, self.code, self.timeout)
        cache.set(self.limit_key, self.code, self.limit)
        logger.debug(f"Send verify code to {self.target}")


class TokenTempCache:
    CACHE_KEY_TOKEN_TEMP_PREFIX = "_KEY_TOKEN_TEMP_CACHE_{}"

    @classmethod
    def generate_cache_token(cls, timeout: int = 3600, data: Any = None) -> Any:
        token = random_string(50)
        key = cls.CACHE_KEY_TOKEN_TEMP_PREFIX.format(token)
        cache.set(key, {"time": time.time(), "data": data}, timeout)
        return token

    @classmethod
    def validate_cache_token(cls, token: Any) -> Any:
        if not token:
            return None
        key = cls.CACHE_KEY_TOKEN_TEMP_PREFIX.format(token)
        value = cache.get(key)
        if not value:
            return None
        try:
            return value.get("data", None)
        except Exception as e:
            logger.error(e, exc_info=True)
            return None

    @classmethod
    def expired_cache_token(cls, token: Any) -> None:
        key = cls.CACHE_KEY_TOKEN_TEMP_PREFIX.format(token)
        cache.delete(key)
