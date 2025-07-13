#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : common
# author : ly_13
# date : 7/12/2025
from contextlib import contextmanager

from django.conf import settings
from django.utils import translation


@contextmanager
def activate_user_language(user):
    language = getattr(user, 'lang', settings.LANGUAGE_CODE)
    with translation.override(language):
        yield
