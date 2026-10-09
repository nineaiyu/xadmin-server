#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : common
# author : ly_13
# date : 9/14/2024

import logging
import os
import socket

import html2text
import psutil


def get_logger(name: str = "") -> logging.Logger:
    if "/" in name:
        name = os.path.basename(name).replace(".py", "")
    return logging.getLogger(f"xadmin.{name}")


def get_disk_usage(path: str) -> float:
    return float(psutil.disk_usage(path=path).percent)


def get_boot_time() -> float:
    return float(psutil.boot_time())


def get_cpu_percent() -> float:
    return float(psutil.cpu_percent())


def get_cpu_load() -> float:
    cpu_load_1, cpu_load_5, cpu_load_15 = psutil.getloadavg()
    cpu_count = psutil.cpu_count()
    single_cpu_load_1 = cpu_load_1 / cpu_count
    single_cpu_load_1 = f"{single_cpu_load_1:.2f}"
    return float(single_cpu_load_1)


def get_docker_mem_usage_if_limit() -> float | None:
    try:
        with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as f:
            limit_in_bytes = int(f.readline())
            total = psutil.virtual_memory().total
            if limit_in_bytes >= total:
                raise ValueError("Not limit")

        with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as f:
            usage_in_bytes = int(f.readline())

        with open("/sys/fs/cgroup/memory/memory.stat") as f:
            inactive_file: int | str = 0
            for line in f:
                if line.startswith("total_inactive_file"):
                    name, inactive_file = line.split()
                    break

                if line.startswith("inactive_file"):
                    name, inactive_file = line.split()
                    continue

            inactive_file = int(inactive_file)
        return ((usage_in_bytes - inactive_file) / limit_in_bytes) * 100

    except Exception:
        # 磁盘用量读取失败：返回 None，调用方按「未知」处理（监控非关键路径）
        return None


def get_memory_usage() -> float:
    usage = get_docker_mem_usage_if_limit()
    if usage is not None:
        return usage
    return float(psutil.virtual_memory().percent)


def get_net_io_bytes() -> tuple[int, int]:
    """网卡累计收发字节 (sent, recv)；采集失败返回 (0, 0) 不中断心跳。

    注意这是累计计数器（进程/系统重启后归零），速率必须由调用方按时间差换算。
    """
    try:
        net = psutil.net_io_counters()
        return int(net.bytes_sent), int(net.bytes_recv)
    except Exception:
        # 网卡计数不可用：返回零值（监控采集降级，不阻断心跳）
        return 0, 0


def test_ip_connectivity(host: str, port: int | str, timeout: float = 0.5) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    result = sock.connect_ex((host, int(port)))
    sock.close()
    if result == 0:
        connectivity = True
    else:
        connectivity = False
    return connectivity


def convert_html_to_markdown(html_str: str) -> str:
    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_links = False

    markdown = str(h.handle(html_str))
    markdown = markdown.replace("\n\n", "\n")
    markdown = markdown.replace("\n ", "\n")
    return markdown
