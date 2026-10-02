#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : logging
# author : ly_13
# date : 10/18/2024
import asyncio
import json
import logging
import os
import re
import shutil
from datetime import UTC, datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

from common.core.sensitive import SENSITIVE_FIELDS
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


class SuppressShieldedCancelledError(logging.Filter):
    """过滤 Python 3.14 asyncio.shield 的断连噪音（asyncio.tasks._log_on_exception）。

    客户端中断在途请求（浏览器页面跳转/关闭的常态）会让 asgiref sync_to_async
    的内层 future 被取消，3.14 起 asyncio 对「shield 后的 future 被 CancelledError
    结束」一律在 asyncio logger 上记 ERROR 并附全栈——属正常流量形态而非故障。
    按「消息前缀 + 异常类型」双匹配精准丢弃，asyncio 的其余日志原样放行。
    """

    def filter(self, record):
        if not record.getMessage().startswith("CancelledError exception in shielded future"):
            return True
        exc_type = record.exc_info[0] if record.exc_info else None
        return exc_type is not asyncio.CancelledError


class SensitiveDataFilter(logging.Filter):
    """运行日志脱敏过滤器（O8-6）：按敏感字段键名对最终输出文本做掩码。

    操作日志链路的请求体/响应体已由 ``oplog_recorder.desensitize_payload`` 收敛，
    本过滤器补齐**运行日志面**：自定义 logger 的 f-string 插值、第三方库拼进消息
    或异常栈的 ``password=…`` / ``"token": "…"` 等键值形态不再明文落盘（日志文件
    保留期长，明文凭证等同泄露）。名单复用 ``common.core.sensitive.SENSITIVE_FIELDS``
    （与操作日志同源）；该模块纯常量零依赖，可在 dictConfig 期安全导入
    （oplog_recorder 本体带 app 注册表依赖，不能在此期 import）。

    匹配三种键值形态：``"password": "x"``（JSON/repr 双引号）、``'password': 'x'``
    （单引号）、``password=x``（kv 串）。带引号的值整段掩码；裸值仅当含非数字
    字符才掩码——业务响应包络 ``code: 200`` 是日志主诊断信息且数字形态并非口令
    常态（``"code": "123456"`` 仍会掩码）。掩码与操作日志同口径：保留原值长度。
    异常栈（exc_text / stack_info）一并处理；过滤器自身故障放行原样，不吞日志。
    """

    _DOUBLE_QUOTED: "re.Pattern[str] | None" = None
    _SINGLE_QUOTED: "re.Pattern[str] | None" = None
    _BARE: "re.Pattern[str] | None" = None

    @classmethod
    def _patterns(cls):
        # 惰性编译一次：键名单为常量，无需模块导入期开销
        if cls._BARE is None:
            alt = "|".join(sorted(SENSITIVE_FIELDS, key=len, reverse=True))
            # sep 允许可选引号：JSON 形态键自带闭引号（"password": "x"），kv 串没有
            sep = r"(?P<sep>\s*[\"']?\s*[:=]\s*)"
            cls._DOUBLE_QUOTED = re.compile(rf'(?P<key>{alt}){sep}"(?P<val>[^"]*)"', re.IGNORECASE)
            cls._SINGLE_QUOTED = re.compile(rf"(?P<key>{alt}){sep}'(?P<val>[^']*)'", re.IGNORECASE)
            # 裸值：取到空白/分隔符为止；空匹配（值缺失或已被前序掩码）在替换函数里原样返回
            cls._BARE = re.compile(rf"(?P<key>{alt}){sep}(?P<val>[^\s'\";,}}]+)", re.IGNORECASE)
        return cls._DOUBLE_QUOTED, cls._SINGLE_QUOTED, cls._BARE

    @classmethod
    def _mask_text(cls, text):
        if not text:
            return text

        def _already_masked(val):
            # 幂等：同一 record 会流经多个 handler（console + 文件），重复掩码不二次变形
            return not val or val.strip("*") == ""

        def _sub_quoted(match):
            val = match.group("val")
            if _already_masked(val):
                return match.group(0)
            # 保留定界引号：match 消费了开闭引号，按原样补回（JSON/repr 形态不破坏）
            quote = match.group(0)[len(match.group("key")) + len(match.group("sep"))]
            return f"{match.group('key')}{match.group('sep')}{quote}{'*' * len(val)}{quote}"

        def _sub_bare(match):
            val = match.group("val")
            if _already_masked(val) or val.isdigit():
                # 纯数字裸值不掩码：业务响应包络 `code: 200` 是日志主诊断信息，
                # 数字形态也非口令常态（带引号的 "code": "123456" 仍会掩码）
                return match.group(0)
            return f"{match.group('key')}{match.group('sep')}{'*' * len(val)}"

        double, single, bare = cls._patterns()
        # 顺序固定：先带引号形态（值含空格也整段掩码），后裸值形态
        text = double.sub(_sub_quoted, text)
        text = single.sub(_sub_quoted, text)
        return bare.sub(_sub_bare, text)

    def filter(self, record):
        try:
            original = record.getMessage()
            masked = self._mask_text(original)
            if masked != original:
                # 消息定稿后清掉 args：格式化阶段直接用掩码后的 msg，不再回插原值
                record.msg, record.args = masked, None
            if record.exc_info:
                # 预生成异常文本并掩码：Formatter 检测到 exc_text 已存在时直接复用
                record.exc_text = self._mask_text(
                    record.exc_text or logging.Formatter().formatException(record.exc_info)
                )
            if record.stack_info:
                record.stack_info = self._mask_text(record.stack_info)
        except Exception:  # noqa: BLE001 脱敏自身故障不阻断日志输出
            return True
        return True


class ServerFormatter(logging.Formatter):
    def format(self, record):
        current_request = get_current_request()
        # 认证中间件之前的异常路径（DisallowedHost 等）请求还没有 user 属性——必须兜底，
        # 否则格式器抛 AttributeError → Logging error，整条记录（含异常栈）被吞掉
        # （2026-09-18 真丢包演练排查时发现：500 的 traceback 曾因此不进日志）
        user = getattr(current_request, "user", None) if current_request else None
        record.requestUser = str(user or "SYSTEM")[:16]
        record.requestUuid = str(getattr(current_request, "request_uuid", ""))
        return super().format(record)


class JsonFormatter(logging.Formatter):
    """结构化 JSON 日志（LOG_FORMAT=json 时启用），供 Loki/ELK 等采集端解析。

    每条记录固定携带 request_uuid / request_user，与响应头 X-Request-Id 对应，
    便于按请求串联网关日志、应用日志与错误上报。
    """

    def format(self, record):
        current_request = get_current_request()
        # 与 ServerFormatter 同口径：无 user 属性的请求（认证前异常路径）兜底 SYSTEM
        user = getattr(current_request, "user", None) if current_request else None
        payload = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "module": f"{record.pathname}:{record.lineno}",
            "process": record.process,
            "thread": record.thread,
            "request_uuid": str(getattr(current_request, "request_uuid", "") or ""),
            "request_user": str(user or "SYSTEM")[:16],
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
