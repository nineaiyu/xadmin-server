# -*- coding: utf-8 -*-
"""单例域装饰器：类级单例。"""


class Singleton:
    """单例类"""

    def __init__(self, cls):
        self._cls = cls
        self._instance = {}

    def __call__(self):
        if self._cls not in self._instance:
            self._instance[self._cls] = self._cls()
        return self._instance[self._cls]
