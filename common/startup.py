#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : startup
# author : ly_13
# date : 9/14/2024

import os
import socket
import threading
import time

from django.conf import settings

from common.core.db.utils import close_old_connections
from common.decorators import Singleton
from common.serializers import MonitorSerializer
from common.utils import (
    get_boot_time,
    get_cpu_load,
    get_cpu_percent,
    get_disk_usage,
    get_memory_usage,
    get_net_io_bytes,
)


class BaseTerminal:
    def __init__(self, suffix_name, _type):
        server_hostname = os.environ.get("SERVER_HOSTNAME") or ""
        hostname = socket.gethostname()
        if server_hostname:
            name = f"[{suffix_name}]-{server_hostname}"
        else:
            name = f"[{suffix_name}]-{hostname}"
        self.name = name
        self.interval = 30
        self.remote_addr = self.get_remote_addr(hostname)
        self.type = _type

    @staticmethod
    def get_remote_addr(hostname):
        try:
            return socket.gethostbyname(hostname)
        except socket.gaierror:
            return "127.0.0.1"

    def start_heartbeat_thread(self):
        print(f"- Start heartbeat thread => ({self.name})")
        t = threading.Thread(target=self.start_heartbeat, daemon=True)
        t.start()

    def start_heartbeat(self):
        while True:
            try:
                net_sent, net_recv = get_net_io_bytes()
                heartbeat_data = {
                    "cpu_load": get_cpu_load(),
                    "cpu_percent": get_cpu_percent(),
                    "memory_used": get_memory_usage(),
                    "disk_used": get_disk_usage(path=settings.PROJECT_DIR),
                    "boot_time": get_boot_time(),
                    "net_sent_mb": round(net_sent / 1024 / 1024, 3),
                    "net_recv_mb": round(net_recv / 1024 / 1024, 3),
                }
                status_serializer = MonitorSerializer(data=heartbeat_data)
                status_serializer.is_valid()
                status_serializer.save()
            except Exception:
                print("Save status error, close old connections")
                close_old_connections()
            finally:
                # 单次 sleep：失败时下轮立刻重试（旧实现在成功分支额外 sleep 一次，
                # 实际落盘周期是 interval 的两倍，与文档/告警检查的 30s 口径不符）
                time.sleep(self.interval)


@Singleton
class CoreTerminal(BaseTerminal):
    def __init__(self):
        super().__init__(suffix_name="Core", _type="core")
