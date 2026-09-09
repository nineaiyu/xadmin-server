#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""任务执行日志增量读取（celery 落盘日志的公共消费入口）。

CeleryThreadTaskFileHandler 按 task_id 落盘 CELERY_LOG_DIR/<task_id>.log，
结束符为 CELERY_LOG_MAGIC_MARK（5 个 \\x00）。任务执行历史页与异步导出下载
中心共用同一套读取逻辑（二者 pk 均为 celery task_id）。
"""

import os

from common.celery.utils import CELERY_LOG_MAGIC_MARK, get_celery_task_log_path

LOG_READ_CHUNK = 64 * 1024


def read_task_log_chunk(task_id, offset=0, finished_hint=False):
    """增量读取一段任务日志。

    Args:
        task_id: celery task id（即执行历史/导出记录主键）
        offset: 上次读取到的字节偏移
        finished_hint: 日志文件尚未生成时的 finished 取值，由调用方按记录终态判断

    Returns:
        dict(offset, finished, content)
    """
    path = get_celery_task_log_path(str(task_id))
    if not os.path.exists(path):
        # 日志尚未落盘（任务还在排队）或已被清理，不报错，交由调用方按终态判 finished
        return {"offset": 0, "finished": bool(finished_hint), "content": ""}
    size = os.path.getsize(path)
    offset = min(max(0, int(offset or 0)), size)
    with open(path, "rb") as fp:
        fp.seek(offset)
        chunk = fp.read(LOG_READ_CHUNK)
    next_offset = offset + len(chunk)
    finished = CELERY_LOG_MAGIC_MARK in chunk
    if finished:
        # 结束标记是落盘控制符，不能作为日志内容返回
        chunk = chunk.replace(CELERY_LOG_MAGIC_MARK, b"")
    return {
        "offset": next_offset,
        "finished": finished,
        "content": chunk.decode("utf-8", errors="replace"),
    }
