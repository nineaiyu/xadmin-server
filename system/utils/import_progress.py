#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""异步导入运行期进度上报（缓存通道）。

为什么不用数据库字段：导入任务是「外层一个大事务 + 每行 savepoint」的原子语义
（失败率超限要回滚全部成功行），事务未提交前其他连接（下载中心）读不到同一事务里
写入的进度，进度条只会在任务结束瞬间从 0 跳到 100。因此运行期进度写缓存（跨连接
可见、无需提交），终态仍落 ImportRecord 字段，由序列化器在 RUNNING 时优先读缓存。
"""

from django.core.cache import cache

# 缓存兜底 TTL：任务异常退出未清理时最多残留 1 小时
IMPORT_PROGRESS_TIMEOUT = 3600


def progress_cache_key(record_id) -> str:
    return f"import_progress_{record_id}"


def set_import_progress(record_id, percent: int) -> None:
    """运行期进度落缓存（0-100）。"""
    cache.set(progress_cache_key(record_id), max(0, min(100, int(percent))), IMPORT_PROGRESS_TIMEOUT)


def get_import_progress(record_id):
    """运行期进度（未上报或已清理返回 None）。"""
    return cache.get(progress_cache_key(record_id))


def clear_import_progress(record_id) -> None:
    cache.delete(progress_cache_key(record_id))
