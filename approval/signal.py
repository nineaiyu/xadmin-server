#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""approval 域信号定义（自 system/signal.py 归位）。

approval_instance_finished（流程实例终态）：由引擎侧 ``_finish_instance``
显式发送（APPROVED / REJECTED / CANCELLED 三个终态的唯一收敛点）。

终态写入走 queryset.update()（条件更新 + 并发占位语义），不触发 post_save，
信号必须显式发送；接收方 approval/signal_handler.py 据此把审批结果回写业务单
（biz_type/biz_id 绑定的实例才会有业务接收方）。
"""

from django.dispatch import Signal

# kwargs = instance/status/reason；只在 biz_type 非空时发送——未绑业务的实例
# 不产生任何回调，引擎与历史用例零影响。
approval_instance_finished = Signal()
