# -*- coding: utf-8 -*-
"""框架内核（``common``）。

分发名 ``xadmin-common``（uv 工作区成员 / 独立分发包），导入包名 ``common``。

``__version__`` 是**分发包版本的单一事实源**：成员 ``pyproject.toml`` 经
``[tool.hatch.version]`` 从这里取值，发布脚本与本目录的 ``CHANGELOG.md`` 由
``tests/unit/test_kernel_release.py`` 锁步。平台版本（服务端整包）另见
``server/const.py`` 的 ``VERSION``，两者独立演进。

发版流程与私有源接入见 ``docs/ops/kernel-release.md``。
"""

__version__ = "0.2.0"
