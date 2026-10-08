#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""外部服务接入域（IM / 短信 / AI 供应商适配器，自 integrations/sdk 迁出）。

common 是框架层基座，不应携带具体供应商的适配器；本 app 只做「协议客户端」：
无模型、无迁移、无菜单、无路由。消费方向：业务 app（ai / mfa / notifications /
settings / message）按需 import 具体客户端；框架层（common）唯一消费点是
验证码短信发送，经函数级惰性 import（门禁逃生门，见 packages/xadmin-common/common/utils/verify_code.py）。
"""
