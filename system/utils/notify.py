#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : notify
# author : ly_13
# date : 9/8/2023

from typing import List, Optional, Literal, Dict

from django.db.models import QuerySet

from system.models import Notification


def base_notify(users: List | QuerySet, title: str, message: str, notify_type: int,
                level: Optional[Literal['success', '', 'warning', 'error']], extra_json: Dict = None):
    if isinstance(users, (QuerySet, list)):
        recipients = users
    else:
        recipients = [users]

    new_notifications = []

    for recipient in recipients:
        notify_obj = Notification(
            owner=recipient,
            title=title,
            message=message,
            level=level,
            notify_type=notify_type,
            extra_json=extra_json
        )
        notify_obj.save()
        new_notifications.append(notify_obj)

    return new_notifications


def notify_success(users: List | QuerySet, title: str, message: str, notify_type: int = 2, extra_json: Dict = None):
    return base_notify(users, title, message, notify_type, 'success', extra_json)


def notify_info(users: List | QuerySet, title: str, message: str, notify_type: int = 2, extra_json: Dict = None):
    return base_notify(users, title, message, notify_type, '', extra_json)


def notify_warning(users: List | QuerySet, title: str, message: str, notify_type: int = 2, extra_json: Dict = None):
    return base_notify(users, title, message, notify_type, 'warning', extra_json)


def notify_error(users: List | QuerySet, title: str, message: str, notify_type: int = 2, extra_json: Dict = None):
    return base_notify(users, title, message, notify_type, 'error', extra_json)
