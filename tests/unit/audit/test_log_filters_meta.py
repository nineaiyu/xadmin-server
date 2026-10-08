# -*- coding: utf-8 -*-
"""日志页时间过滤口径锁定：登录日志与操作日志的 created_time 过滤器同为范围口径。

两过滤器均继承 ``packages/xadmin-common/common/core/filter.py::BaseFilterSet``，其显式声明的
``created_time = DateTimeFromToRangeFilter`` 优先于 Meta.fields 生成的同名精确
过滤器；据此前端两个日志页的时间过滤交互保持一致（起止区间，而非单点精确）。
此测试把该口径钉住，防止某页单独漂移回 exact 造成两页口径不一致。
"""

from django_filters.filters import DateTimeFromToRangeFilter

from audit.views.admin.loginlog import LoginLogFilter
from audit.views.admin.operationlog import OperationLogFilter


def test_loginlog_created_time_is_range_filter():
    """登录日志页 created_time 为起止范围过滤（与操作日志页同口径）。"""
    assert isinstance(LoginLogFilter.base_filters["created_time"], DateTimeFromToRangeFilter)


def test_operationlog_created_time_is_range_filter():
    """操作日志页 created_time 为起止范围过滤（与登录日志页同口径）。"""
    assert isinstance(OperationLogFilter.base_filters["created_time"], DateTimeFromToRangeFilter)
