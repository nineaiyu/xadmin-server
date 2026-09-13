#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""出站 Webhook（ADR-022）：订阅与投递审计模型。"""

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

    def __str__(self):
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
    status = models.CharField(_("Status"), max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempt = models.IntegerField(_("Attempt"), default=0)
    response_code = models.IntegerField(_("Response code"), null=True, blank=True)
    response_body = models.CharField(_("Response body"), max_length=500, blank=True, default="")
    duration = models.FloatField(_("Duration"), null=True, blank=True, help_text=_("Seconds"))
    next_retry_at = models.DateTimeField(_("Next retry at"), null=True, blank=True)

    class Meta:
        verbose_name = _("Webhook delivery")
        verbose_name_plural = _("Webhook deliveries")
        ordering = ("-created_time",)
        indexes = [models.Index(fields=["subscription", "created_time"], name="idx_webhook_sub_created")]

    def __str__(self):
        return f"{self.subscription_id}:{self.event}:{self.status}"
