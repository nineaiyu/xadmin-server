#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : heatbeat
# author : ly_13
# date : 10/23/2024
import os.path
import tempfile
from pathlib import Path

from celery.signals import heartbeat_sent, worker_ready, worker_shutdown

temp_dir = tempfile.gettempdir()


@heartbeat_sent.connect
def heartbeat(sender, **kwargs):
    worker_name = sender.eventer.hostname.split('@')[0]
    heartbeat_path = Path(os.path.join(temp_dir, f'worker_heartbeat_{worker_name}'))
    heartbeat_path.touch()


@worker_ready.connect
def worker_ready(sender, **kwargs):
    worker_name = sender.hostname.split('@')[0]
    ready_path = Path(os.path.join(temp_dir, f'worker_ready_{worker_name}'))
    ready_path.touch()


@worker_shutdown.connect
def worker_shutdown(sender, **kwargs):
    worker_name = sender.hostname.split('@')[0]
    for signal in ['ready', 'heartbeat']:
        path = Path(os.path.join(temp_dir, f'worker_{signal}_{worker_name}'))
        path.unlink(missing_ok=True)
