import datetime
import hashlib
import random
import time
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.encoding import smart_str

from captcha.helpers import get_challenge

# Heavily based on session key generation in Django
# Use the system (hardware-based) random number generator if it exists.
if hasattr(random, "SystemRandom"):
    randrange = random.SystemRandom().randrange
else:
    randrange = random.randrange
MAX_RANDOM_KEY = 18446744073709551616  # 2 << 63

from django.utils.translation import gettext_lazy as _

from common.core.models import DbUuidModel
from common.utils import get_logger

logger = get_logger(__name__)


class CaptchaStore(models.Model):
    id = models.AutoField(primary_key=True)
    challenge = models.CharField(blank=False, max_length=32)
    response = models.CharField(blank=False, max_length=32)
    hashkey = models.CharField(blank=False, max_length=40, unique=True)
    expiration = models.DateTimeField(blank=False)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.response = self.response.lower()
        if not self.expiration:
            self.expiration = timezone.now() + datetime.timedelta(minutes=int(settings.CAPTCHA_TIMEOUT))
        if not self.hashkey:
            key_ = (
                smart_str(randrange(0, MAX_RANDOM_KEY))
                + smart_str(time.time())
                + smart_str(self.challenge, errors="ignore")
                + smart_str(self.response, errors="ignore")
            ).encode("utf8")
            self.hashkey = hashlib.sha1(key_).hexdigest()
            del key_
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return str(self.challenge)

    @classmethod
    def remove_expired(cls) -> None:
        cls.objects.filter(expiration__lte=timezone.now()).delete()

    @classmethod
    def generate_key(cls, generator: Any = None) -> str:
        challenge, response = get_challenge(generator)()
        store = cls.objects.create(challenge=challenge, response=response)

        hashkey: str = store.hashkey
        return hashkey

    @classmethod
    def pick(cls) -> str:
        if not settings.CAPTCHA_GET_FROM_POOL:
            return cls.generate_key()

        def fallback() -> str:
            logger.error("Couldn't get a captcha from pool, generating")
            return cls.generate_key()

        # Pick up a random item from pool
        minimum_expiration = timezone.now() + datetime.timedelta(minutes=int(settings.CAPTCHA_GET_FROM_POOL_TIMEOUT))
        store = cls.objects.filter(expiration__gt=minimum_expiration).order_by("?").first()

        hashkey: str = (store and store.hashkey) or fallback()
        return hashkey

    @classmethod
    def create_pool(cls, count: int = 1000) -> None:
        assert count > 0
        while count > 0:
            cls.generate_key()
            count -= 1


class CaptchaLog(DbUuidModel):
    """验证码全链路事件留痕（观测层）。

    异步写入（Celery 批量落库），遥测故障不影响验证结果；风险分只进本表
    与票据，不向客户端回显。字段语义见
    ``docs/plans/验证码企业级增强方案-2026.10.md`` 观测层一节。
    """

    class PhaseChoices(models.TextChoices):
        CHALLENGE = "challenge", _("Challenge issued")
        VERIFY = "verify", _("Answer verified")
        CONSUME = "consume", _("Ticket consumed")
        DECISION = "decision", _("Risk decision")

    ts = models.DateTimeField(verbose_name=_("Event time"), default=timezone.now)
    phase = models.CharField(verbose_name=_("Phase"), max_length=16, choices=PhaseChoices.choices)
    scene = models.CharField(verbose_name=_("Scene"), max_length=32, blank=True, default="")
    action = models.CharField(verbose_name=_("Action"), max_length=64, blank=True, default="")
    mode = models.CharField(verbose_name=_("Risk mode"), max_length=16, blank=True, default="")
    type = models.CharField(verbose_name=_("Captcha type"), max_length=16, blank=True, default="")
    difficulty = models.CharField(verbose_name=_("Difficulty"), max_length=8, blank=True, default="")
    decision = models.CharField(verbose_name=_("Decision"), max_length=16, blank=True, default="")
    would_decision = models.CharField(verbose_name=_("Shadow decision"), max_length=16, blank=True, default="")
    risk_score = models.IntegerField(verbose_name=_("Risk score"), null=True, blank=True)
    risk_reasons = models.JSONField(verbose_name=_("Risk reasons"), default=list, blank=True)
    passed = models.BooleanField(verbose_name=_("Passed"), default=False)
    attempts = models.IntegerField(verbose_name=_("Attempts"), default=0)
    latency_ms = models.IntegerField(verbose_name=_("Latency (ms)"), default=0)
    ip_hash = models.CharField(verbose_name=_("IP hash"), max_length=64, blank=True, default="")
    fp_hash = models.CharField(verbose_name=_("Fingerprint hash"), max_length=64, blank=True, default="")
    ua_class = models.CharField(verbose_name=_("UA class"), max_length=32, blank=True, default="")
    browser = models.CharField(verbose_name=_("Browser"), max_length=64, blank=True, default="")
    env_version = models.CharField(verbose_name=_("Env version"), max_length=16, blank=True, default="")
    pow_bits = models.IntegerField(verbose_name=_("PoW bits"), null=True, blank=True)
    key_id = models.CharField(verbose_name=_("Key ID"), max_length=16, blank=True, default="")

    class Meta:
        verbose_name = _("Captcha log")
        verbose_name_plural = verbose_name
        ordering = ["-ts"]
        indexes = [
            models.Index(fields=["ts"]),
            models.Index(fields=["decision", "ts"]),
            models.Index(fields=["fp_hash", "ts"]),
        ]

    @classmethod
    def purge_expired_logs(cls, retention_days: int | None = None) -> int:
        """删除超过保留期的观测日志；返回删除行数（保留期 0 = 不清理）。"""
        from django.conf import settings

        if retention_days is None:
            retention_days = int(getattr(settings, "CAPTCHA_LOG_RETENTION_DAYS", 30) or 0)
        if retention_days <= 0:
            return 0
        horizon = timezone.now() - datetime.timedelta(days=retention_days)
        deleted, _ = cls.objects.filter(ts__lte=horizon).delete()
        return int(deleted)


class CaptchaRule(DbUuidModel):
    """风险规则（DB 可配：分值 / 开关 / 优先级热更，不发版）。

    规则判据在 ``captcha/risk.py`` 的 16 个可解释信号求值器中实现；
    本表只承载运营可调项，避免把任意表达式（eval）引入运行时。
    ``params`` 用于窗口/阈值等规则私有参数（JSON）。
    """

    code = models.CharField(verbose_name=_("Rule code"), max_length=32, unique=True)
    name = models.CharField(verbose_name=_("Rule name"), max_length=64)
    scope = models.CharField(
        verbose_name=_("Scope"), max_length=32, blank=True, default="", help_text=_("Empty = all scenes")
    )
    score = models.IntegerField(verbose_name=_("Score"), default=0)
    enabled = models.BooleanField(verbose_name=_("Enabled"), default=True)
    priority = models.IntegerField(verbose_name=_("Priority"), default=100)
    params = models.JSONField(verbose_name=_("Parameters"), default=dict, blank=True)

    class Meta:
        verbose_name = _("Captcha rule")
        verbose_name_plural = verbose_name
        ordering = ["priority", "code"]
        indexes = [models.Index(fields=["enabled", "priority"])]

    def __str__(self) -> str:
        return f"{self.code}({self.score})"
