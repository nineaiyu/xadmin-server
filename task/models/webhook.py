#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""出站 Webhook：订阅与投递审计模型。"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel


class WebhookSubscription(DbAuditModel, DbUuidModel):
    """Webhook 订阅：按事件类型 POST 签名 JSON 到订阅方 URL。"""

    name = models.CharField(_("Name"), max_length=128, unique=True)
    url = models.CharField(_("URL"), max_length=512)
    # per-row secret：signer 值级加密落库（写入侧加密，投递时解密）
    secret = models.CharField(_("Secret"), max_length=512)
    events = models.JSONField(_("Events"), default=list, help_text=_("Event keys from EVENT_CATALOG"))
    description = models.CharField(_("Description"), max_length=512, blank=True, default="")
    is_active = models.BooleanField(_("Is active"), default=True)
    last_failure = models.CharField(_("Last failure"), max_length=512, blank=True, default="")

    class Meta:
        verbose_name = _("Webhook subscription")
        verbose_name_plural = _("Webhook subscriptions")
        ordering = ("-created_time",)

    def __str__(self) -> str:
        return f"{self.name}({self.url})"


class WebhookDelivery(DbAuditModel, DbUuidModel):
    """投递审计：每次投递尝试的最终态与最近一次响应摘要。"""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SUCCESS = "success", _("Success")
        FAILED = "failed", _("Failed (retrying)")
        EXHAUSTED = "exhausted", _("Exhausted")

    subscription = models.ForeignKey(
        WebhookSubscription, on_delete=models.CASCADE, related_name="deliveries", verbose_name=_("Subscription")
    )
    event = models.CharField(_("Event"), max_length=64, db_index=True)
    payload = models.JSONField(_("Payload"), default=dict)
    # 发送时的签名头快照（事件/投递 id/时间戳/签名）：审计页据此自证「发了什么、怎么签的」
    request_headers = models.JSONField(_("Request headers"), default=dict, blank=True)
    status = models.CharField(_("Status"), max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempt = models.IntegerField(_("Attempt"), default=0)
    response_code = models.IntegerField(_("Response code"), null=True, blank=True)
    response_body = models.TextField(_("Response body"), blank=True, default="")
    #: 响应体超过审计存储上限（发送侧保留完整文本的前 8KB）
    response_body_truncated = models.BooleanField(_("Response body truncated"), default=False)
    duration = models.FloatField(_("Duration"), null=True, blank=True, help_text=_("Seconds"))
    next_retry_at = models.DateTimeField(_("Next retry at"), null=True, blank=True)
    # 派发代际号：每次派发（首投/倒计时重试/人工重试）事务内 +1，任务只认领
    # 派发时携带的代际号——队列中残留的旧任务到期后因不匹配静默失效，
    # 避免与人工 retry 并发造成订阅方收到计划外重复投递
    generation = models.IntegerField(_("Generation"), default=0)

    class Meta:
        verbose_name = _("Webhook delivery")
        verbose_name_plural = _("Webhook deliveries")
        ordering = ("-created_time",)
        indexes = [models.Index(fields=["subscription", "created_time"], name="idx_webhook_sub_created")]

    def __str__(self) -> str:
        return f"{self.subscription_id}:{self.event}:{self.status}"
