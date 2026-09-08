#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""任务执行日志 WebSocket 增量推送。

前端连接 ws/tasks/log/<execution pk> 后，服务端从 0 增量推送该执行的 celery
日志文件内容；CeleryThreadTaskFileHandler 在任务收尾时把 CELERY_LOG_MAGIC_MARK
追加为文件最后 5 字节（logger.handle_task_end），读到即代表输出完成：
- 鉴权复用 asgi 的 Cookie 认证中间件（scope['user']），未登录 4401 拒绝；
- 文件未落盘时与 HTTP log action 同语义：执行已结束即 finished，否则持续等待；
- 读文件循环采用 message/notify.py 既有 tail 模式（aiofiles + asyncio.sleep），
  但必须以独立 task 运行（connect 内阻塞会导致 disconnect 事件永远排队）。
"""

import asyncio
import os

import aiofiles
from channels.db import database_sync_to_async

from common.celery.utils import CELERY_LOG_MAGIC_MARK, get_celery_task_log_path
from common.utils import get_logger
from message.base import AsyncJsonWebsocket
from message.protocol import MessageAction
from system.models.task import TaskExecution

logger = get_logger(__name__)

LOG_READ_CHUNK = 64 * 1024
PUSH_INTERVAL = 1


@database_sync_to_async
def _execution_finished(pk):
    """执行是否已有终态时间（文件缺失或无结束标记时判断是否还需等待）。"""
    finished = TaskExecution.objects.filter(pk=pk).values_list("date_finished", flat=True).first()
    return finished is not None


async def _tail_has_mark(path):
    """日志文件最后 5 字节是否为结束标记。"""
    size = os.path.getsize(path)
    if size < len(CELERY_LOG_MAGIC_MARK):
        return False
    async with aiofiles.open(path, "rb") as fp:
        await fp.seek(size - len(CELERY_LOG_MAGIC_MARK))
        return await fp.read(len(CELERY_LOG_MAGIC_MARK)) == CELERY_LOG_MAGIC_MARK


class TaskLogNotify(AsyncJsonWebsocket):
    """一条连接只服务一条执行记录：从 0 增量推送到输出完成。"""

    async def connect(self):
        self.user = self.scope["user"]
        if not self.user:
            await self.close(4401)
            return
        self.pk = self.scope["url_route"]["kwargs"]["pk"]
        self.offset = 0
        self.disconnected = False
        await self.accept()
        asyncio.create_task(self.push_log_loop())

    async def disconnect(self, close_code):
        self.disconnected = True

    async def ping(self, event):
        """任务日志连接不属于消息层分组（无 group_name），心跳无需登记，
        静默忽略即可；沿用基类实现会因缺少 group_name 抛 AttributeError 断连。"""

    async def push_log_loop(self):
        path = get_celery_task_log_path(str(self.pk))
        try:
            while not self.disconnected:
                if await self.push_once(path):
                    break
                await asyncio.sleep(PUSH_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("TaskLog push failed: %s", self.pk)
        finally:
            if not self.disconnected:
                await self.close()

    async def push_once(self, path):
        """推送一次增量，返回 True 表示输出已完成、循环可结束。"""
        size = os.path.getsize(path) if os.path.exists(path) else 0
        if self.offset < size:
            async with aiofiles.open(path, "rb") as fp:
                await fp.seek(self.offset)
                chunk = await fp.read(LOG_READ_CHUNK)
            self.offset += len(chunk)
            content = chunk.replace(CELERY_LOG_MAGIC_MARK, b"").decode("utf-8", errors="replace")
            finished = self.offset >= size and await _tail_has_mark(path)
            await self.send_base_json(
                MessageAction.TASK_LOG.value,
                {
                    "offset": self.offset,
                    "content": content,
                    "finished": finished,
                },
            )
            return finished
        # 已读到文件尾（或文件尚未创建）
        finished = await _tail_has_mark(path) if size else False
        if not finished:
            finished = await _execution_finished(self.pk)
        await self.send_base_json(
            MessageAction.TASK_LOG.value,
            {
                "offset": self.offset,
                "content": "",
                "finished": finished,
            },
        )
        return finished
