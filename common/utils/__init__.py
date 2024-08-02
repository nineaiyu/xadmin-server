#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : __init__
# author : ly_13
# date : 7/31/2024
import logging
import os


def get_logger(name=''):
    if '/' in name:
        name = os.path.basename(name).replace('.py', '')
    return logging.getLogger('xadmin.%s' % name)
