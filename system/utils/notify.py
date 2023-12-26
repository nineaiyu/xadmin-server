#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notify
# author : ly_13
# date : 9/8/2023

from typing import List, Optional, Literal, Dict

from django.db.models import QuerySet

from system.models import NoticeMessage

SYSTEM = NoticeMessage.NoticeChoices.SYSTEM

def base_notify(users: List | QuerySet, title: str, message: str, notice_type: int,
                level: Optional[Literal['success', '', 'warning', 'error']], extra_json: Dict = None):
    if isinstance(users, (QuerySet, list)):
        recipients = users
    else:
        recipients = [users]

    notify_obj = NoticeMessage.objects.create(
        title=title,
        message=message,
        level=level,
        notice_type=notice_type,
        extra_json=extra_json
    )
    notify_obj.notice_user.set(recipients)
    return notify_obj


def notify_success(users: List | QuerySet, title: str, message: str, notice_type: int = SYSTEM,
                   extra_json: Dict = None):
    return base_notify(users, title, message, notice_type, 'success', extra_json)


def notify_info(users: List | QuerySet, title: str, message: str, notice_type: int = SYSTEM, extra_json: Dict = None):
    return base_notify(users, title, message, notice_type, '', extra_json)


def notify_warning(users: List | QuerySet, title: str, message: str, notice_type: int = SYSTEM,
                   extra_json: Dict = None):
    return base_notify(users, title, message, notice_type, 'warning', extra_json)


def notify_error(users: List | QuerySet, title: str, message: str, notice_type: int = SYSTEM, extra_json: Dict = None):
    return base_notify(users, title, message, notice_type, 'error', extra_json)
