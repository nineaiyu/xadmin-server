#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""approval 域信号接收器：流程实例终态回写业务单（自 system/signal_handler.py 归位）。

按 ``instance.biz_type`` 在 ``approval/biz_sync.py`` 注册表解析业务同步器分发；
业务 app 通过自身 config.py 的 ``APPROVAL_BIZ_SYNCERS`` 声明接入，新增业务
无需改动本文件（注册表与内置声明见 biz_sync 模块说明）。回写失败只记日志
——业务状态由审批结果驱动，不应反过来阻断审批。
"""

from django.dispatch import receiver

from approval.biz_sync import get_biz_syncer
from approval.signal import approval_instance_finished
from common.utils import get_logger

logger = get_logger(__name__)


@receiver(approval_instance_finished)
def sync_business_status_handler(sender, instance, status=None, reason="", **kwargs):
    """流程实例终态 → 业务同步器（biz_type 认领；未注册的 biz_type 记警告跳过）。"""
    biz_type = getattr(instance, "biz_type", "")
    if not biz_type:
        return
    syncer = get_biz_syncer(biz_type)
    if syncer is None:
        logger.warning("no business sync handler for biz_type:%s", biz_type)
        return
    try:
        syncer(instance, status, reason)
    except Exception:
        logger.exception(
            "sync business status failed. instance:%s biz_type:%s", getattr(instance, "pk", None), biz_type
        )
