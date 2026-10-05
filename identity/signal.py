#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""identity 域信号定义。"""

import django.dispatch

# 用户权限/路由缓存失效信号：settings 侧个人配置变更等场景发送，
# 接收方 identity/signal_handler（登出信号 user_logged_out 同源处置）
invalid_user_cache_signal = django.dispatch.Signal()
