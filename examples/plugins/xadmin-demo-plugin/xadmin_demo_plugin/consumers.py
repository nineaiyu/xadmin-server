#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""插件 WebSocket 消费者（示例）：``ws/plugin-demo/`` → 就绪回执。

二开 WS 通道约定：

- 只用 channels 公开基类（``AsyncJsonWebsocketConsumer``），不 import 宿主业务 app
  的内部实现（跨 app 耦合是二开最常见的返工来源）；
- **必须自行鉴权**：内核的 ASGI 栈完成认证并注入 ``scope["user"]``，但准入判断在
  消费者一侧——未登录直接 ``close(4401)``（与内核既有口径一致）；
- 模块停用时本通道由 ``ModuleTrimWebsocketMiddleware`` 在准入层拒绝（4404），
  消费者不会收到 connect。
"""

from typing import Any

from channels.generic.websocket import AsyncJsonWebsocketConsumer


class PluginDemoConsumer(AsyncJsonWebsocketConsumer):
    """示例通道：连接就绪回执（演示二开 WS 通道的接入与裁剪）。"""

    async def connect(self) -> None:
        user = self.scope.get("user")
        if user is None or not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return
        await self.accept()
        await self.send_json({"action": "plugin_demo_ready", "data": {"module": "demo_plugin"}})

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        # 示例：原样回显（生产插件请按 protocol 约定定义 action 白名单）
        await self.send_json({"action": "plugin_demo_echo", "data": content})
