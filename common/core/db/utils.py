#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : utils
# author : ly_13
# date : 12/18/2023
from contextlib import contextmanager

from django.db import connections, transaction, connection


class RelatedManager:
    """规则 Q 构建的兼容外观：实现已迁至 common/core/data_scope.py（含通配与 m2m_all 修复）。

    保留本类是为了既有引用（测试等）不破；新代码请直接使用 data_scope 的
    rule_to_q / ip_in_q / build_rules_qs。
    """

    def __init__(self, instance, field):
        self.instance = instance
        self.field = field
        self.value = None

    def set(self, value):
        self.value = value
        self.instance.__dict__[self.field.name] = value

    @staticmethod
    def get_ip_in_q(name, val):
        from common.core.data_scope import ip_in_q

        return ip_in_q(name, val)

    @classmethod
    def get_filter_attrs_qs(cls, rules):
        from common.core.data_scope import build_rules_qs

        return build_rules_qs(rules)


def close_old_connections(**kwargs):
    for conn in connections.all(initialized_only=True):
        conn.close_if_unusable_or_obsolete()


# 这个要是在 Django 请求周期外使用的，不能影响 Django 的事务管理， 在 api 中使用会影响 api 事务
@contextmanager
def safe_db_connection():
    close_old_connections()
    yield
    close_old_connections()


@contextmanager
def safe_atomic_db_connection(auto_close=False):
    """
    通用数据库连接管理器（线程安全、事务感知）：
    - 在连接不可用时主动重建连接
    - 在非事务环境下自动关闭连接（可选）
    - 不影响 Django 请求/事务周期
    """
    in_atomic = connection.in_atomic_block  # 当前是否在事务中
    autocommit = transaction.get_autocommit()
    recreated = False

    try:
        if not connection.is_usable():
            connection.close()
            connection.connect()
            recreated = True
        yield
    finally:
        # 只在非事务、autocommit 模式下，才考虑主动清理连接
        if auto_close or (recreated and not in_atomic and autocommit):
            close_old_connections()


@contextmanager
def open_db_connection(alias="default"):
    connection = transaction.get_connection(alias)
    try:
        connection.connect()
        with transaction.atomic():
            yield connection
    finally:
        connection.close()
