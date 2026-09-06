#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : apps
from django.apps import AppConfig


class MfaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mfa'
