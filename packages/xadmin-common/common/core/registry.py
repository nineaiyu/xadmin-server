#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""可重置注册表原语（内核）：内置项 + 运行期扩展项的统一容器。

此前的扩展点（审批 biz_type 渲染器 / AI 动作 / 通知渠道）形态各异：模块级 dict、
元组常量、进程级缓存——二开者每种都要读实现源码才能确定「怎么加、加了是否生效、
测试怎么清理」。本原语统一口径：

- ``register`` 键冲突 fail-fast（静默覆盖是扩展点最难排查的故障形态）；
- ``reset`` 丢弃扩展项回到内置态（测试隔离用；内置项不可注销）；
- 读侧 ``get`` / ``items`` / ``keys`` / ``values`` 与 dict 口径一致，存量调用面零迁移。

仅覆盖「扁平映射」形态；需要排序/分层语义的注册表（如 AI 动作发现）保留各自
实现，只用本原语收敛行为约定。
"""

from collections.abc import Iterator, Mapping
from typing import Any


class Registry:
    """内置项 + 扩展项的可重置注册表（读取时扩展项覆盖同名内置项由 ``register`` 的
    ``override`` 显式声明，默认冲突即抛）。"""

    def __init__(self, name: str, builtins: Mapping[Any, Any]) -> None:
        self._name = name
        self._builtins: dict[Any, Any] = dict(builtins)
        self._extra: dict[Any, Any] = {}

    def register(self, key: Any, value: Any, *, override: bool = False) -> None:
        """登记扩展项；键与内置或既有扩展冲突且未显式 override 时 fail-fast。"""
        if not override and (key in self._builtins or key in self._extra):
            raise ValueError(f"registry {self._name!r}: key {key!r} already declared")
        self._extra[key] = value

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self._extra:
            return self._extra[key]
        return self._builtins.get(key, default)

    def reset(self) -> None:
        """丢弃全部扩展项（测试隔离用；内置项保持）。"""
        self._extra.clear()

    def items(self) -> Any:
        return {**self._builtins, **self._extra}.items()

    def keys(self) -> Any:
        return {**self._builtins, **self._extra}.keys()

    def values(self) -> Any:
        return {**self._builtins, **self._extra}.values()

    def __iter__(self) -> Iterator[Any]:
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self._builtins) + len(self._extra)

    def __contains__(self, key: Any) -> bool:
        return key in self._builtins or key in self._extra
