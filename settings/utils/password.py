#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : password
# author : ly_13
# date : 8/10/2024
import re

from django.conf import settings


def get_password_check_rules(user):
    check_rules = []
    for rule in settings.SECURITY_PASSWORD_RULES:
        if user.is_superuser and rule == 'SECURITY_PASSWORD_MIN_LENGTH':
            rule = 'SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH'
        value = getattr(settings, rule)
        if not value:
            continue
        check_rules.append({'key': rule, 'value': int(value)})
    return check_rules


def check_password_rules(password, is_super_admin=False):
    pattern = r"^"
    if settings.SECURITY_PASSWORD_UPPER_CASE:
        pattern += r'(?=.*[A-Z])'
    if settings.SECURITY_PASSWORD_LOWER_CASE:
        pattern += r'(?=.*[a-z])'
    if settings.SECURITY_PASSWORD_NUMBER:
        pattern += r'(?=.*\d)'
    if settings.SECURITY_PASSWORD_SPECIAL_CHAR:
        pattern += r'(?=.*[`~!@#$%^&*()\-=_+\[\]{}|;:\'",.<>/?])'
    pattern += r'[a-zA-Z\d`~!@#\$%\^&\*\(\)-=_\+\[\]\{\}\|;:\'\",\.<>\/\?]'
    if is_super_admin:
        min_length = settings.SECURITY_ADMIN_USER_PASSWORD_MIN_LENGTH
    else:
        min_length = settings.SECURITY_PASSWORD_MIN_LENGTH
    pattern += '.{' + str(min_length - 1) + ',}$'
    match_obj = re.match(pattern, password)
    return bool(match_obj)
