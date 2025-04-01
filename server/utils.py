#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 10/18/2024

from django.conf import settings
from django.db import connection
from django.db.backends.utils import truncate_name
from django.db.models.signals import class_prepared

from common.local import thread_local


def set_current_request(request):
    setattr(thread_local, 'current_request', request)


def _find(attr):
    return getattr(thread_local, attr, None)


def get_current_request():
    return _find('current_request')


def add_db_prefix(sender, **kwargs):
    prefix = settings.DB_PREFIX
    meta = sender._meta
    if not meta.managed:
        return
    if isinstance(prefix, dict):
        app_label = meta.app_label.lower()
        if meta.label_lower in prefix:
            prefix = prefix[meta.label_lower]
        elif meta.label in prefix:
            prefix = prefix[meta.label]
        elif app_label in prefix:
            prefix = prefix[app_label]
        else:
            prefix = prefix.get("", None)
    if prefix and not meta.db_table.startswith(prefix):
        meta.db_table = truncate_name("%s%s" % (prefix, meta.db_table), connection.ops.max_name_length())


class_prepared.connect(add_db_prefix)
