#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : tests
# author : ly_13
# date : 12/23/2023
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'server.settings')
django.setup()

from system.models import *
from django.core import management

# 如果有用户存在，则不支持初始化操作
try:
    if UserInfo.objects.exists():
        print(f'User already exists')
        exit(-1)
except Exception as e:
    print(e)
    pass

# 初始化操作
try:
    management.call_command('makemigrations', )
    management.call_command('migrate', )
    # management.call_command('collectstatic', )
    management.call_command('compilemessages', )
    management.call_command('download_ip_db', )
except Exception as e:
    print(f'Perform migrate failed, {e} exit')

# 创建是默认管理员用户，请及时修改信息
UserInfo.objects.create_superuser('xadmin', 'xadmin@dvcloud.xin', 'xAdminPwd!')

management.call_command('load_init_json', )

# 加载默认用户数据，一般部署新服的时候，如果有默认数据，则可以进行加载
# management.call_command('loaddata', 'loadjson/userinfo.json')
