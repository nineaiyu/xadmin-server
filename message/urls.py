#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""聊天室 REST 路由（/api/chat/，ADR-034）。"""

from rest_framework.routers import SimpleRouter

from message.views import ChatAiViewSet, ChatContactViewSet, ChatMessageViewSet, ChatRoomViewSet

app_name = "chat"

# 与 system / notifications 一致：SimpleRouter(False) 表示 URL 不带尾斜杠
# （/api/chat/room、/api/chat/message/{id}/recall），权限点 path 也按此口径登记
router = SimpleRouter(False)
router.register("room", ChatRoomViewSet, basename="chat-room")
router.register("message", ChatMessageViewSet, basename="chat-message")
router.register("contacts", ChatContactViewSet, basename="chat-contact")
router.register("ai", ChatAiViewSet, basename="chat-ai")

urlpatterns = router.urls
