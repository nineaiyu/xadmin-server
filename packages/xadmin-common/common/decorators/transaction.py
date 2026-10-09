# -*- coding: utf-8 -*-
"""事务域装饰器：事务提交后执行。"""

from collections.abc import Callable
from typing import Any

from django.db import transaction


def on_transaction_commit(func: Callable[..., Any]) -> Callable[..., None]:
    """
    如果不调用on_commit, 对象创建时添加多对多字段值失败
    """

    def inner(*args: Any, **kwargs: Any) -> None:
        transaction.on_commit(lambda: func(*args, **kwargs))

    return inner
