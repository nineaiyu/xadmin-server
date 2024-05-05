#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notify
# author : ly_13
# date : 9/8/2023

from typing import List, Dict

from django.db.models import QuerySet

from common.core.config import UserConfig
from message.utils import push_message
from system.models import NoticeMessage
from system.utils.serializer import NoticeMessageSerializer

SYSTEM = NoticeMessage.NoticeChoices.SYSTEM


def push_notice_messages(notify_obj, pks):
    notice_message = NoticeMessageSerializer(
        fields=['pk', 'level', 'title', 'notice_type', 'message'],
        instance=notify_obj).data
    notice_message['message_type'] = 'notify_message'
    for pk in pks:
        if UserConfig(pk).PUSH_MESSAGE_NOTICE:
            push_message(pk, notice_message)
    return notify_obj


def base_notify(users: List | QuerySet, title: str, message: str, notice_type: int,
                level: NoticeMessage.LevelChoices, extra_json: Dict = None):
    if isinstance(users, (QuerySet, list)):
        recipients = users
    else:
        recipients = [users]

    notify_obj = NoticeMessage.objects.create(
        title=title,
        publish=True,
        message=message,
        level=level,
        notice_type=notice_type,
        extra_json=extra_json
    )
    notify_obj.notice_user.set(recipients)
    push_notice_messages(notify_obj, [user.pk for user in recipients])
    return notify_obj


def notify_success(users: List | QuerySet, title: str, message: str, notice_type: int = SYSTEM,
                   extra_json: Dict = None):
    return base_notify(users, title, message, notice_type, NoticeMessage.LevelChoices.SUCCESS, extra_json)


def notify_info(users: List | QuerySet, title: str, message: str, notice_type: int = SYSTEM, extra_json: Dict = None):
    return base_notify(users, title, message, notice_type, NoticeMessage.LevelChoices.PRIMARY, extra_json)


def notify_error(users: List | QuerySet, title: str, message: str, notice_type: int = SYSTEM, extra_json: Dict = None):
    return base_notify(users, title, message, notice_type, NoticeMessage.LevelChoices.DANGER, extra_json)
