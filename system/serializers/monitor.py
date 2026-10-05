#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""服务器心跳序列化器（采集线程写 Monitor 前的校验面，自 common 迁出）。"""

from rest_framework import serializers

from system.models.monitor import Monitor


class MonitorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Monitor
        fields = [
            "cpu_load",
            "cpu_percent",
            "memory_used",
            "disk_used",
            "boot_time",
            "net_sent_mb",
            "net_recv_mb",
            "created_time",
        ]
        extra_kwargs = {
            "cpu_load": {"default": 0},
            "cpu_percent": {"default": 0},
            "memory_used": {"default": 0},
            "disk_used": {"default": 0},
            "boot_time": {"default": 0},
            "net_sent_mb": {"default": 0},
            "net_recv_mb": {"default": 0},
        }
