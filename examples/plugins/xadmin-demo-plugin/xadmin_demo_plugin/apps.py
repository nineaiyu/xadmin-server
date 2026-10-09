#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""插件 AppConfig：应用装配与契约注入（INSTALLED_APPS 路径）。

装配链（宿主侧）：``config.yml`` 的 ``XADMIN_APPS`` 追加本插件后，
``server/settings/apps.py::build_installed_apps`` 把它插到 ``common.apps.CommonConfig``
之前 → Django 逐 app ``ready()``：本插件 ``ready()`` 先于 ``common.ready()``（entry point
契约装配点）执行，注入即刻生效。

两条注入路径的取舍（见 ``providers.py`` 与 ``docs/guide/plugin-development.md``）：

- **entry point**：宿主零接线、随包分发；适合「装包即生效」的通用覆盖；
- **AppConfig.ready()**：本文件的形态；适合需要精确控制注入时序、或要在注入前
  读取宿主配置做条件判断的场景。

同一契约**不可**同时经两条路径注入：``register_contract`` 对重复注册 fail-fast
（防二开包之间静默互踩）。本插件按契约拆分：``get_active_superuser_queryset`` 走
entry point，``guarded_models`` 走 ready()。
"""

from django.apps import AppConfig


class XadminDemoPluginConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "xadmin_demo_plugin"
    verbose_name = "示例二开插件"

    def ready(self) -> None:
        """把插件模型的引用保护并入内核「删除影响面守卫」契约。"""
        from common.contracts import register_contract
        from xadmin_demo_plugin.providers import guarded_models

        register_contract("guarded_models", guarded_models)
