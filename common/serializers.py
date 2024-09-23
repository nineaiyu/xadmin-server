#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializers
# author : ly_13
# date : 9/14/2024
from rest_framework import serializers

from common.models import Monitor


class MonitorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Monitor
        fields = ['cpu_load', 'cpu_percent', 'memory_used', 'disk_used', 'boot_time', 'created_time']
        extra_kwargs = {
            "cpu_load": {'default': 0},
            "cpu_percent": {'default': 0},
            "memory_used": {'default': 0},
            "disk_used": {'default': 0},
            "boot_time": {'default': 0},
        }
