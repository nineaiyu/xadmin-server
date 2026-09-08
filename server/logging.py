#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : logging
# author : ly_13
# date : 10/18/2024
import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler

from server.utils import get_current_request

# 按天目录滚动（rotator 把旧日志移入 日期/ 子目录）后，
# TimedRotatingFileHandler 标准的 backupCount 清理逻辑扫不到子目录，需自行按目录清理
_DATED_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DailyTimedRotatingFileHandler(TimedRotatingFileHandler):
    def rotator(self, source, dest):
        """Override the original method to rotate the log file daily."""
        dest = self._get_rotate_dest_filename(source)
        if os.path.exists(source) and not os.path.exists(dest):
            # 存在多个服务进程时, 保证只有一个进程成功 rotate
            os.rename(source, dest)
        self._prune_dated_dirs(source)

    def _prune_dated_dirs(self, source):
        """超出 backupCount 的历史日期目录整体清理（0 或负数表示不清理）。"""
        backup_count = getattr(self, "backupCount", 0) or 0
        if backup_count <= 0:
            return
        log_dir = os.path.dirname(source)
        try:
            dated_dirs = sorted(
                (
                    name
                    for name in os.listdir(log_dir)
                    if _DATED_DIR_RE.match(name) and os.path.isdir(os.path.join(log_dir, name))
                ),
                reverse=True,
            )
        except OSError:
            return
        for name in dated_dirs[backup_count:]:
            shutil.rmtree(os.path.join(log_dir, name), ignore_errors=True)

    @staticmethod
    def _get_rotate_dest_filename(source):
        date_yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        path = [os.path.dirname(source), date_yesterday, os.path.basename(source)]
        filename = os.path.join(*path)
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        return filename


class ServerFormatter(logging.Formatter):
    def format(self, record):
        current_request = get_current_request()
        record.requestUser = str(current_request.user if current_request else "SYSTEM")[:16]
        record.requestUuid = str(getattr(current_request, "request_uuid", ""))
        return super().format(record)


class JsonFormatter(logging.Formatter):
    """结构化 JSON 日志（LOG_FORMAT=json 时启用），供 Loki/ELK 等采集端解析。

    每条记录固定携带 request_uuid / request_user，与响应头 X-Request-Id 对应，
    便于按请求串联网关日志、应用日志与错误上报。
    """

    def format(self, record):
        current_request = get_current_request()
        payload = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "module": f"{record.pathname}:{record.lineno}",
            "process": record.process,
            "thread": record.thread,
            "request_uuid": str(getattr(current_request, "request_uuid", "") or ""),
            "request_user": str(current_request.user if current_request else "SYSTEM")[:16],
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ColorHandler(logging.StreamHandler):
    WHITE = "0"
    RED = "31"
    GREEN = "32"
    YELLOW = "33"
    BLUE = "34"
    PURPLE = "35"

    def emit(self, record):
        try:
            msg = self.format(record)
            level_color_map = {
                logging.DEBUG: self.BLUE,
                logging.INFO: self.GREEN,
                logging.WARNING: self.YELLOW,
                logging.ERROR: self.RED,
                logging.CRITICAL: self.PURPLE,
            }

            csi = f"{chr(27)}["  # 控制序列引入符
            color = level_color_map.get(record.levelno, self.WHITE)

            self.stream.write(f"{csi}{color}m{msg}{csi}m\n")
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)
