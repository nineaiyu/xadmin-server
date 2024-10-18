#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 10/18/2024

from functools import partial

from werkzeug.local import LocalProxy

from common.local import thread_local


def set_current_request(request):
    setattr(thread_local, 'current_request', request)


def _find(attr):
    return getattr(thread_local, attr, None)


def get_current_request():
    return _find('current_request')


current_request = LocalProxy(partial(_find, 'current_request'))
