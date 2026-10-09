#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""示例二开插件（xadmin 二开实战）。

本插件同时演示三条二开接入通道（互不依赖，按需取用）：

1. **契约注入 · entry point**（``pyproject.toml`` 的 ``[project.entry-points."xadmin.contracts"]``）：
   覆盖内核契约 ``get_active_superuser_queryset``——宿主零接线，装包即生效；
2. **契约注入 · AppConfig.ready()**（``apps.py``）：覆盖内核契约 ``guarded_models``——
   需要精确注入时序（先于消费方 import）时走这条；
3. **应用级扩展点**：``config.py::URLPATTERNS``（路由自动注入）、``modules.py``（功能模块
   声明，参与模块裁剪与发行预设）、``tasks.py``（周期任务）、``routing.py``（WS 通道）。

契约面白名单与解析序见 ``packages/xadmin-common/common/contracts.py``；
模块声明与六层裁剪见 ``packages/xadmin-common/common/core/modules/``；
安装与验证步骤见本目录 ``README.md`` 与 ``docs/guide/plugin-development.md``。
"""

__version__ = "0.1.0"
