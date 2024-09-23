#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : common
# author : ly_13
# date : 9/14/2024

import logging
import os

import psutil


def get_logger(name=''):
    if '/' in name:
        name = os.path.basename(name).replace('.py', '')
    return logging.getLogger('xadmin.%s' % name)


class lazyproperty:
    def __init__(self, func):
        self.func = func

    def __get__(self, instance, cls):
        if instance is None:
            return self
        else:
            value = self.func(instance)
            setattr(instance, self.func.__name__, value)
            return value


def get_disk_usage(path):
    return psutil.disk_usage(path=path).percent


def get_boot_time():
    return psutil.boot_time()


def get_cpu_percent():
    return psutil.cpu_percent()

def get_cpu_load():
    cpu_load_1, cpu_load_5, cpu_load_15 = psutil.getloadavg()
    cpu_count = psutil.cpu_count()
    single_cpu_load_1 = cpu_load_1 / cpu_count
    single_cpu_load_1 = '%.2f' % single_cpu_load_1
    return float(single_cpu_load_1)


def get_docker_mem_usage_if_limit():
    try:
        with open('/sys/fs/cgroup/memory/memory.limit_in_bytes') as f:
            limit_in_bytes = int(f.readline())
            total = psutil.virtual_memory().total
            if limit_in_bytes >= total:
                raise ValueError('Not limit')

        with open('/sys/fs/cgroup/memory/memory.usage_in_bytes') as f:
            usage_in_bytes = int(f.readline())

        with open('/sys/fs/cgroup/memory/memory.stat') as f:
            inactive_file = 0
            for line in f:
                if line.startswith('total_inactive_file'):
                    name, inactive_file = line.split()
                    break

                if line.startswith('inactive_file'):
                    name, inactive_file = line.split()
                    continue

            inactive_file = int(inactive_file)
        return ((usage_in_bytes - inactive_file) / limit_in_bytes) * 100

    except Exception:
        return None


def get_memory_usage():
    usage = get_docker_mem_usage_if_limit()
    if usage is not None:
        return usage
    return psutil.virtual_memory().percent
