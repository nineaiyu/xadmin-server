#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 供应商接入 SDK（ADR-023）：OpenAI 兼容 chat/completions 客户端。

与 common/sdk/sms、common/sdk/im 对称：凭据注入、http 客户端可注入（单测
离线）、供应商原始报文只进日志（可读异常）。协议差异大的供应商后续按
flavor 扩展。
"""
