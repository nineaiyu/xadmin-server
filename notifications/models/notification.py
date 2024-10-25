#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notification
# author : ly_13
# date : 9/13/2024

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class UserMsgSubscription(DbAuditModel):
    message_type = models.CharField(max_length=128, verbose_name=_('message type'))
    user = models.ForeignKey('system.UserInfo', related_name='user_msg_subscription', on_delete=models.CASCADE,
                             verbose_name=_('User'))
    receive_backends = models.JSONField(default=list, verbose_name=_('receive backend'))

    class Meta:
        verbose_name = _('User message subscription')
        unique_together = (('user', 'message_type'),)

    def __str__(self):
        return _('{} subscription').format(self.user)


class SystemMsgSubscription(DbAuditModel):
    message_type = models.CharField(max_length=128, unique=True, verbose_name=_('message type'))
    users = models.ManyToManyField('system.UserInfo', related_name='system_msg_subscriptions', verbose_name=_("User"))
    receive_backends = models.JSONField(default=list, verbose_name=_('receive backend'))

    class Meta:
        verbose_name = _('System message subscription')

    def __str__(self):
        return f'{self.message_type} -- {self.receive_backends}'
