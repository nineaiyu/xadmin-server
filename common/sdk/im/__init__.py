#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""企业 IM 发送 SDK（ADR-019）：钉钉工作通知 / 企微应用消息 / 飞书 IM 消息。

与 common/sdk/sms 对称：凭据与 token 管理收口在客户端内，http 客户端可注入
（单测离线），错误语义（企微/钉钉 errcode、飞书 code）统一判定后抛可读异常。
"""
