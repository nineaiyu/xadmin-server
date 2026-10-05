#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal
# author : ly_13
# date : 10/11/2024

"""platform 域信号定义。

invalid_user_cache_signal（用户缓存失效）已随 identity 域拆分至 identity/signal.py；
approval_instance_finished（审批终态回写）已随 approval 域归位至 approval/signal.py
（接收器同源迁移 approval/signal_handler.py，业务回写注册表 approval/biz_sync.py）。
"""

# 本文件当前无 platform 域自有信号；identity / approval 各域信号见对应 app 的 signal 模块。
