#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""契约提供方：注入内核契约面的实现对象（二开注入制的「被注入对象」）。

覆盖的两条契约都在 ``packages/xadmin-common/common/contracts.py`` 白名单内：

- ``get_active_superuser_queryset``：告警收件人集合（默认 = 在用超管）。本插件把它
  收敛到宿主配置的账号白名单——**经 entry point 注入**，装包即生效；
- ``guarded_models``：删除前要求影响面确认的模型清单（默认 = ``IMPACT_GUARD_MODELS``
  配置项）。本插件把自有模型并入该清单——**经 ``AppConfig.ready()`` 注入**。

实现边界（二开注入的通用约束）：

- 白名单外的契约名会在启动期直接报错，不做静默降级；
- 提供方函数签名必须与内核默认实现一致（覆盖是「换实现」不是「换接口」）；
- 取默认实现请**直接 import 提供方模块**（``identity.services`` / ``audit.services``），
  不要经 ``common.contracts`` 解析——后者会解析到本插件自身，形成递归。
"""

from typing import Any

from django.conf import settings

#: 告警收件人白名单配置键（宿主 config.yml / settings 提供；空列表 = 不收敛，与内核默认一致）
ALERT_ALLOWLIST_SETTING = "DEMO_PLUGIN_ALERT_ALLOWLIST"


def get_active_superuser_queryset() -> Any:
    """告警收件人 = 在用超管 ∩ 插件白名单（未配置白名单时行为与内核默认完全一致）。"""
    from identity.services import get_active_superuser_queryset as default_provider

    queryset = default_provider()
    allowlist = list(getattr(settings, ALERT_ALLOWLIST_SETTING, []) or [])
    if not allowlist:
        return queryset
    return queryset.filter(username__in=allowlist)


def guarded_models() -> set[str]:
    """内核默认守卫清单 + 插件自有模型（插件模型的删除同样需要影响面确认）。"""
    from audit.services import guarded_models as default_provider
    from xadmin_demo_plugin.models import PluginNote

    return set(default_provider()) | {PluginNote._meta.label_lower}
