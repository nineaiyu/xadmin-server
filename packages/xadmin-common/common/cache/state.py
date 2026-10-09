#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : state
# author : ly_13
# date : 6/2/2023

import time
from typing import Any

from django.core.cache import cache

from common.utils import get_logger

logger = get_logger(__name__)


class CacheBaseState:
    def __init__(self, key: str, value: Any = None, timeout: int = 3600 * 24) -> None:
        self.key = f"CacheBaseState_{self.__class__.__name__}_{key}"
        # 默认值不能在参数默认值处求值（那会在模块导入时刻固定），否则所有实例共享同一时间戳
        self.value = time.time() if value is None else value
        self.timeout = timeout
        self.active = False

    def get_state(self) -> Any:
        return cache.get(self.key)

    def del_state(self) -> Any:
        return cache.delete(self.key)

    def __enter__(self) -> bool:
        if cache.get(self.key):
            return False
        else:
            cache.set(self.key, self.value, self.timeout)
            self.active = True
        return True

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.active:
            cache.delete(self.key)
        logger.info(f"cache base state __exit__ {exc_type}, {exc_val}, {exc_tb}")


class SyncDriveSizeState(CacheBaseState): ...


class GetDriveAuthCache(CacheBaseState): ...
