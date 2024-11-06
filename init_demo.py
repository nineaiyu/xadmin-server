#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : tests
# author : ly_13
# date : 12/23/2023
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'server.settings')
django.setup()

from system.models import *
from django.core import management

import os

# 如果有用户存在，则不支持初始化操作
try:
    if UserInfo.objects.exists():
        print(f'User already exists')
        exit(-1)
except:
    pass

# 需要删除所有表
try:
    os.unlink('./db.sqlite3')
except OSError as e:
    print(e)

# 初始化操作
try:
    management.call_command('makemigrations', )
    management.call_command('migrate', )
    # management.call_command('collectstatic', )
    management.call_command('compilemessages', )
    management.call_command('download_ip_db', )
except Exception as e:
    print(f'Perform migrate failed, {e} exit')

UserInfo.objects.create_superuser('xadmin', 'demon@xadmin.com', '123456')

management.call_command('load_init_json', )
management.call_command('loaddata', 'loadjson/userinfo.json')
