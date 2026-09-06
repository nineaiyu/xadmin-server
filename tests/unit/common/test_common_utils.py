# -*- coding: utf-8 -*-
"""common/utils/common.py：系统信息采集与 HTML 转 Markdown。"""
import socket

from common.utils import common as common_utils

get_boot_time = common_utils.get_boot_time
get_cpu_load = common_utils.get_cpu_load
get_cpu_percent = common_utils.get_cpu_percent
get_disk_usage = common_utils.get_disk_usage
get_memory_usage = common_utils.get_memory_usage
convert_html_to_markdown = common_utils.convert_html_to_markdown


def test_get_disk_usage_returns_percent():
    usage = get_disk_usage("/")
    assert 0 <= usage <= 100


def test_get_boot_time_is_positive_epoch():
    assert get_boot_time() > 0


def test_get_cpu_percent_between_0_and_100():
    assert 0 <= get_cpu_percent() <= 100


def test_get_cpu_load_non_negative():
    assert get_cpu_load() >= 0


def test_get_memory_usage_between_0_and_100():
    # macOS / 非 cgroup 环境回退 psutil；cgroup 环境走 docker 路径
    assert 0 <= get_memory_usage() <= 100


def _connects(host: str, port: int, timeout: float = 0.5) -> bool:
    # 函数名以 test_ 开头会被 pytest 收集，经模块引用调用
    return common_utils.test_ip_connectivity(host, port, timeout=timeout)


def test_test_ip_connectivity_open_port():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert _connects("127.0.0.1", port) is True
    finally:
        server.close()


def test_test_ip_connectivity_closed_port():
    assert _connects("127.0.0.1", 1, timeout=0.2) is False


def test_convert_html_to_markdown_basic():
    markdown = convert_html_to_markdown("<h1>标题</h1><p>正文 <b>加粗</b></p>")
    assert "标题" in markdown
    assert "加粗" in markdown


def test_convert_html_to_markdown_keeps_links():
    markdown = convert_html_to_markdown('<a href="https://example.com">链接</a>')
    assert "https://example.com" in markdown
    assert "链接" in markdown
