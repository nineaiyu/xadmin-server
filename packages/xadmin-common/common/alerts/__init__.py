#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""告警回投族：备份失败告警（backup）与运维事件告警（ops）。

同族归组（自顶层平铺的 backup_alert.py / ops_alert.py 迁入）；两路告警均为
「无登录态上报端点 → 节流 → 站内信 + 渠道通知」形态，实现仍各自独立成文件。
"""

from .backup import (
    BACKUP_ALERT_THROTTLE_SECONDS,
    BackupFailureMessage,
    notify_backup_failure,
)
from .ops import OPS_ALERT_THROTTLE_SECONDS, OpsAlertMessage, notify_ops_alert

__all__ = [
    "BACKUP_ALERT_THROTTLE_SECONDS",
    "OPS_ALERT_THROTTLE_SECONDS",
    "BackupFailureMessage",
    "OpsAlertMessage",
    "notify_backup_failure",
    "notify_ops_alert",
]
