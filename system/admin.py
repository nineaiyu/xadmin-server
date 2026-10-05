#!/usr/bin/env python
# -*- coding:utf-8 -*-
from django.contrib import admin

from system.models import (
    DataPermission,
    FieldPermission,
    Menu,
    MenuMeta,
    ModelLabelField,
    OperationLog,
    SystemConfig,
    UserLoginLog,
    UserPersonalConfig,
)

admin.site.register(ModelLabelField)
admin.site.register(UserLoginLog)
admin.site.register(OperationLog)
admin.site.register(MenuMeta)
admin.site.register(Menu)
admin.site.register(DataPermission)
admin.site.register(FieldPermission)
admin.site.register(SystemConfig)
admin.site.register(UserPersonalConfig)
