#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : local
# author : ly_13
# date : 10/18/2024


from asgiref.local import Local

# 上下文本地存储：thread_critical=False → contextvars 存储。
# 依据：ASGI 异步中间件链（RequestMiddleware __acall__）在事件循环任务里写入
# current_request，asgiref SyncToAsync 会把当前 context 复制进同步视图线程
# （thread_sensitive 同线程执行），使 26+ 消费方（serializers 字段权限 / 日志
# formatter / 信号 / response.requestId）照常读到；新起 OS 线程 context 为空、
# Celery 任务线程各持独立 context，与原 thread_critical=True 的线程隔离语义一致。
# 同线程内跨 sync_to_async 的读写从「不可见」变「可见」，属修复而非破坏。
thread_local = Local()


def _find(attr):
    return getattr(thread_local, attr, None)
