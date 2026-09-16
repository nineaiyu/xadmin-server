#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""测试日志隔离守护：发布窗口硬门禁（CSP enforce / AES v1 关闭）的清零判据
依赖生产 `data/logs/server.log`，测试流量必须写入隔离目录（tmp/test_logs/）。

回归背景：pytest 集成用例用服务端加密器构造登录 / 改密请求（产出 v1 格式密文）、
单测断言违规上报会被记录，此前与生产共用 LOGGING 导致 server.log 里的
`CSP violation:` / `aes_v1_decrypt_used` 计数长期被测试污染，清零判据不可判。
"""

from pathlib import Path

from tests.logging_isolation import FILE_HANDLERS


def test_file_handlers_point_to_isolated_dir():
    """三个文件 handler（server / drf_exception / unexpected_exception）都不得再指向 data/logs。"""
    from django.conf import settings

    production_log_dir = str(settings.LOG_DIR)
    for name in FILE_HANDLERS:
        filename = settings.LOGGING["handlers"][name]["filename"]
        assert "test_logs" in filename, f"{name} handler 未接入隔离目录：{filename}"
        assert not filename.startswith(production_log_dir), f"{name} handler 仍写生产日志目录：{filename}"


def test_log_records_land_in_isolated_file():
    """实际写一条日志，确认落盘位置为隔离目录。"""
    import logging

    from django.conf import settings

    probe = "logging-isolation-probe"
    logger = logging.getLogger("xadmin")
    logger.warning(probe)
    for handler in logger.handlers:
        handler.flush()

    target = Path(settings.LOGGING["handlers"]["server"]["filename"])
    assert target.exists(), f"隔离日志文件不存在：{target}"
    assert probe in target.read_text(encoding="utf-8"), "日志记录未落入隔离文件"
