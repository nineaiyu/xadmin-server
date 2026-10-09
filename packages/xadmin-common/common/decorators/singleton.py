# -*- coding: utf-8 -*-
"""单例域装饰器：类级单例。"""

from typing import Any


class Singleton:
    """单例类"""

    def __init__(self, cls: type[Any]) -> None:
        self._cls = cls
        self._instance: dict[type[Any], Any] = {}

    def __call__(self) -> Any:
        if self._cls not in self._instance:
            self._instance[self._cls] = self._cls()
        return self._instance[self._cls]
