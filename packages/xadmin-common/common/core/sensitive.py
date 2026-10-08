#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""敏感字段名单单一事实源（自 oplog_recorder 下拆出）。

名单原挂在 ``common.core.oplog_recorder``（操作日志脱敏）。运行日志脱敏
过滤器（``server.logging.SensitiveDataFilter``）需要同一份名单，但 oplog_recorder
模块级有 OperationLog 元数据求值等依赖 app 注册表的重活——logging dictConfig 在
``apps.populate`` 之前实例化 LOGGING 引用的类，不能 import 它。故把名单下沉为
本模块（纯常量、零依赖），两条脱敏链路共用：

- 操作日志请求体/响应体：``oplog_recorder.desensitize_payload``（键名递归掩码）；
- 运行日志（DEBUG / 自定义 logger / 异常栈）：``server.logging.SensitiveDataFilter``
  （键名正则掩码）。
"""

# code：二次验证提交体里的登录密码/动态验证码（POST /api/mfa/confirm 等），
# token / verify_token：临时令牌与验证码票据（登录/注册/重置/绑定加密握手），
# access / refresh：登录响应里的 JWT（响应快照同口径收敛），
# 严禁明文落日志
SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "old_password",
        "new_password",
        "sure_password",
        "access",
        "refresh",
        "code",
        "token",
        "verify_token",
    }
)
