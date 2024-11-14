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
from common.utils import get_cpu_load, get_memory_usage, get_disk_usage, get_boot_time, get_cpu_percent


class BaseTerminal(object):

    def __init__(self, suffix_name, _type):
        server_hostname = os.environ.get('SERVER_HOSTNAME') or ''
        hostname = socket.gethostname()
        if server_hostname:
            name = f'[{suffix_name}]-{server_hostname}'
        else:
            name = f'[{suffix_name}]-{hostname}'
        self.name = name
        self.interval = 30
        self.remote_addr = self.get_remote_addr(hostname)
        self.type = _type

    @staticmethod
    def get_remote_addr(hostname):
        try:
            return socket.gethostbyname(hostname)
        except socket.gaierror:
            return '127.0.0.1'

    def start_heartbeat_thread(self):
        print(f'- Start heartbeat thread => ({self.name})')
        t = threading.Thread(target=self.start_heartbeat, daemon=True)
        t.start()

    def start_heartbeat(self):
        while True:
            heartbeat_data = {
                'cpu_load': get_cpu_load(),
                'cpu_percent': get_cpu_percent(),
                'memory_used': get_memory_usage(),
                'disk_used': get_disk_usage(path=settings.PROJECT_DIR),
                'boot_time': get_boot_time(),
            }
            status_serializer = MonitorSerializer(data=heartbeat_data)
            status_serializer.is_valid()

            try:
                status_serializer.save()
                time.sleep(self.interval)
            except Exception:
                print("Save status error, close old connections")
                close_old_connections()
            finally:
                time.sleep(self.interval)


@Singleton
class CoreTerminal(BaseTerminal):

    def __init__(self):
        super().__init__(suffix_name='Core', _type='core')
