#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : heatbeat
# author : ly_13
# date : 10/23/2024
import os.path
import tempfile
from pathlib import Path
from typing import Any

from celery.signals import heartbeat_sent, worker_ready, worker_shutdown

temp_dir = tempfile.gettempdir()


@heartbeat_sent.connect  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def heartbeat(sender: Any, **kwargs: Any) -> None:
    worker_name = sender.eventer.hostname.split("@")[0]
    heartbeat_path = Path(os.path.join(temp_dir, f"worker_heartbeat_{worker_name}"))
    heartbeat_path.touch()


@worker_ready.connect  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def on_worker_ready(sender: Any, **kwargs: Any) -> None:
    worker_name = sender.hostname.split("@")[0]
    ready_path = Path(os.path.join(temp_dir, f"worker_ready_{worker_name}"))
    ready_path.touch()


@worker_shutdown.connect  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
def on_worker_shutdown(sender: Any, **kwargs: Any) -> None:
    worker_name = sender.hostname.split("@")[0]
    for signal in ["ready", "heartbeat"]:
        path = Path(os.path.join(temp_dir, f"worker_{signal}_{worker_name}"))
        path.unlink(missing_ok=True)
