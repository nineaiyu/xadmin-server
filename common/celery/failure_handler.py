#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""
Celery 任务失败告警。

通过 task_failure 信号捕获 worker 内的任务异常，经 notifications 通道
（站内信 + 邮件）告知超管；同一任务 60 秒内只告警一次，防止失败风暴刷屏。
"""
import logging

from celery.signals import task_failure
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from notifications.backends import BACKEND
from notifications.models import SystemMsgSubscription
from notifications.notifications import SystemMessage
from system.models import UserInfo

logger = logging.getLogger('xadmin')

FAILURE_THROTTLE_SECONDS = 60
# 通知投递任务自身的失败不再告警，避免告警递归
IGNORE_TASKS = {'notifications.notifications.publish_task'}


class TaskFailureMessage(SystemMessage):
    category = 'Monitor'
    category_label = _('Monitor')
    message_type_label = _('Celery task failure')

    def __init__(self, task_name, exc, traceback_msg):
        self.task_name = task_name
        self.exc = exc
        self.traceback_msg = (traceback_msg or '')[-2000:]

    def get_html_msg(self) -> dict:
        subject = _('Celery task failure alert: {}').format(self.task_name)
        message = (
            f"<p>{_('Task')}: <code>{self.task_name}</code></p>"
            f"<p>{_('Error')}: <code>{self.exc}</code></p>"
            f"<pre style='white-space:pre-wrap'>{self.traceback_msg}</pre>"
        )
        return {'subject': subject, 'message': message}

    def get_site_msg_msg(self):
        info = self.get_html_msg()
        info['level'] = 'danger'
        return info

    @classmethod
    def post_insert_to_db(cls, subscription: SystemMsgSubscription):
        admins = UserInfo.objects.filter(is_superuser=True, is_active=True)
        subscription.users.add(*admins)
        subscription.receive_backends = [BACKEND.SITE_MSG, BACKEND.EMAIL]
        subscription.save()

    @classmethod
    def gen_test_msg(cls):
        return cls('demo_task', 'demo exception', 'Traceback (demo): task failed')


@task_failure.connect
def send_task_failure_alert(sender=None, exception=None, traceback=None, **kwargs):
    task_name = getattr(sender, 'name', '') or ''
    if not task_name or task_name in IGNORE_TASKS:
        return

    # 同一任务节流，防止连续失败产生告警风暴
    throttle_key = f'task_failure_alert_{task_name}'
    if not cache.add(throttle_key, 1, FAILURE_THROTTLE_SECONDS):
        return

    try:
        message = TaskFailureMessage(
            task_name, exception, str(traceback) if traceback else ''
        )
        message.publish(is_async=True)
    except Exception:
        # 告警链路自身异常不能影响 worker 执行
        logger.warning('Send task failure alert error', exc_info=True)
