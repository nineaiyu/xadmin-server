#!/usr/bin/env python
# -*- coding:utf-8 -*-
from django.contrib import admin

from identity.models import DeptInfo, Post, UserInfo, UserRole

admin.site.register(UserInfo)
admin.site.register(DeptInfo)
admin.site.register(Post)
admin.site.register(UserRole)
