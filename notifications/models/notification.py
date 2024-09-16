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
    user = models.OneToOneField('system.UserInfo', on_delete=models.CASCADE, verbose_name=_('User'),
                                related_name='user_msg_subscription')
    receive_backends = models.JSONField(default=list, verbose_name=_('receive backend'))

    class Meta:
        verbose_name = _('User message subscription')

    def __str__(self):
        return _('{} subscription').format(self.user)


class SystemMsgSubscription(DbAuditModel):
    message_type = models.CharField(max_length=128, unique=True, verbose_name=_('message type'))
    users = models.ManyToManyField('system.UserInfo', related_name='system_msg_subscriptions')
    groups = models.ManyToManyField('system.DeptInfo', related_name='system_msg_subscriptions')
    receive_backends = models.JSONField(default=list, verbose_name=_('receive backend'))

    message_type_label = ''

    class Meta:
        verbose_name = _('System message subscription')

    def set_message_type_label(self):
        # 采用手动调用，没设置成 property 的方式
        # 因为目前只有界面修改时会用到这个属性，避免实例化时占用资源计算
        from ..notifications import system_msgs
        msg_label = ''
        for msg in system_msgs:
            if msg.get('message_type') == self.message_type:
                msg_label = msg.get('message_type_label', '')
                break
        self.message_type_label = msg_label

    @property
    def receivers(self):
        from notifications.backends import BACKEND

        users = [user for user in self.users.all()]

        for group in self.groups.all():
            for user in group.users.all():
                users.append(user)

        receive_backends = self.receive_backends
        receviers = []

        for user in users:
            recevier = {'name': str(user), 'id': user.id}
            for backend in receive_backends:
                recevier[backend] = bool(BACKEND(backend).get_account(user))
            receviers.append(recevier)

        return receviers

    def __str__(self):
        return f'{self.message_type_label}' or f'{self.message_type}'

    def __repr__(self):
        return self.__str__()
