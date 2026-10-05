#!/usr/bin/env python
# -*- coding:utf-8 -*-
from django.contrib import admin

from audit.models import DataMaskRule, OperationLog, UserLoginLog

admin.site.register(DataMaskRule)
admin.site.register(OperationLog)
admin.site.register(UserLoginLog)
