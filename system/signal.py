#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : signal
# author : ly_13
# date : 10/11/2024

"""platform 域信号定义。

invalid_user_cache_signal（用户缓存失效）已随 identity 域拆分至 identity/signal.py。
"""

from django.dispatch import Signal

# 流程实例到达终态（APPROVED/REJECTED/CANCELLED）：kwargs = instance/status/reason。
# 终态写入走 queryset.update()（不触发 post_save），故由 _finish_instance 显式发送；
# 业务模块据此把审批结果回写自己的业务单（biz_type/biz_id 绑定的实例才会有业务接收方）。
approval_instance_finished = Signal()
