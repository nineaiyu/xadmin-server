#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""「测试连接」表单值传参公共件（T03-09）。

邮箱 / IM / LDAP 三个测试端点此前口径不一：邮箱只读已存配置（表单里的服务器
信息被忽略），IM / LDAP 临时 ``setattr(settings, ...)`` 改进程全局再恢复——
测试请求与真实请求共享进程全局，并发期间真实请求可能读到测试值。

统一口径：按表单值（未提交键回退已存配置）构造**只读快照**传给客户端构造
参数，绝不触碰进程全局 settings。
"""

from typing import Any

from django.conf import settings


def build_test_values(
    validated_data: dict,
    request_data: dict,
    keys,
    secret_keys=(),
) -> dict[str, Any]:
    """构造测试用生效配置快照：表单值优先，未提交键回落运行时已存值。

    - ``keys`` 中的键：``request_data`` 显式提交的取 ``validated_data`` 值，
      否则取 django settings 当前值；
    - ``secret_keys``（write_only 密文）：表单重新输入（非空）才覆盖，
      留空 = 沿用已存值；
    - 返回普通 dict，调用方按值传参构造客户端/连接/配置快照。
    """
    secret_keys = set(secret_keys)
    values: dict[str, Any] = {}
    # 密文键允许不在 keys 中显式出现（如 LDAP 密码只作回退语义）——也要纳入快照
    for key in list(keys) + sorted(secret_keys - set(keys)):
        if key in secret_keys:
            submitted = validated_data.get(key)
            values[key] = submitted or getattr(settings, key, None)
        elif key in request_data:
            values[key] = validated_data.get(key)
        else:
            values[key] = getattr(settings, key, None)
    return values
