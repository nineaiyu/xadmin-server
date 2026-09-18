#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""大屏远程控制：状态缓存 + WebSocket 下行通道。

- 管理端经 `POST /api/system/screens/{pk}/command` 下发指令（切换仪表盘 / 翻页 /
  刷新 / 恢复轮播），控制态落缓存（TTL 24h）后广播到 `screen_display_{pk}` 组；
- 展示端（大屏页面）连接 `ws/screen/<pk>`：连接即回放当前控制态，保证后开的
  展示端与最近一次指令一致；断线重连同样以回放对齐；
- 准入与 HTTP 可见性同口径（超管 / 创建者 / shared），个人大屏不向他人开放展示通道；
- 展示端为被动接收：不上行指令、不做在线登记（组名不在个人推送组命名空间内，
  不会混入在线列表统计）。
"""

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from message.base import AsyncJsonWebsocket
from message.protocol import MessageAction

logger = get_logger(__name__)

# 控制态缓存时长（秒）：超时未再下发即回到展示端默认轮播
SCREEN_STATE_TTL = 3600 * 24

SCREEN_COMMANDS = ("switch", "page", "refresh", "auto")


def screen_group_name(screen_pk) -> str:
    return f"screen_display_{screen_pk}"


def screen_state_key(screen_pk) -> str:
    return f"screen_display_state_{screen_pk}"


def load_screen_state(screen_pk) -> dict:
    """当前控制态（无指令时返回默认：自动轮播、第 0 页）。"""
    state = cache.get(screen_state_key(screen_pk)) or {}
    return {
        "mode": state.get("mode") or "auto",
        "index": int(state.get("index") or 0),
        "refresh_rev": int(state.get("refresh_rev") or 0),
        "rev": int(state.get("rev") or 0),
        "ts": state.get("ts") or "",
    }


def apply_screen_command(screen, command: str, dashboard_pk: str = "", index=None) -> dict:
    """计算控制帧并落态（管理端侧调用；广播由调用方执行）。

    - switch：切到指定仪表盘（必须在该大屏的 dashboards 清单内）→ manual；
    - page：翻到指定页（0 基，范围校验）→ manual；
    - auto：恢复自动轮播（从当前页继续）；
    - refresh：只递增 refresh_rev（展示端重拉数据），不改变浏览位置与模式。
    """
    if command not in SCREEN_COMMANDS:
        raise DjangoValidationError(_("Unknown screen command"))
    dashboards = [str(item) for item in (screen.dashboards or [])]
    state = load_screen_state(screen.pk)
    if command == "switch":
        target = str(dashboard_pk or "")
        if target not in dashboards:
            raise DjangoValidationError(_("Dashboard is not in this screen"))
        state["index"] = dashboards.index(target)
        state["mode"] = "manual"
    elif command == "page":
        try:
            target_index = int(index)
        except (TypeError, ValueError):
            raise DjangoValidationError(_("Invalid page index")) from None
        if dashboards and not 0 <= target_index < len(dashboards):
            raise DjangoValidationError(_("Page index out of range"))
        state["index"] = target_index
        state["mode"] = "manual"
    elif command == "auto":
        state["mode"] = "auto"
    elif command == "refresh":
        state["refresh_rev"] += 1
    state["rev"] += 1
    state["ts"] = timezone.now().isoformat()
    cache.set(screen_state_key(screen.pk), state, SCREEN_STATE_TTL)
    frame = {"command": command, **state}
    return frame


def broadcast_screen_command(screen_pk, frame: dict) -> None:
    """把控制帧投递到该大屏的展示连接组（展示端不在线时静默丢弃）。"""
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        screen_group_name(screen_pk), {"type": MessageAction.SCREEN_COMMAND.value, "data": frame}
    )


def can_view_screen(user, screen_pk) -> bool:
    """展示通道准入（与 HTTP _visible_queryset 同口径）：超管 / 创建者 / shared 可见。"""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    from system.models.dataset import Screen

    screen = Screen.objects.filter(pk=screen_pk).first()
    if screen is None:
        return False
    if getattr(user, "is_superuser", False) or screen.creator_id == user.pk:
        return True
    return screen.visibility == "shared"


class ScreenDisplayNotify(AsyncJsonWebsocket):
    """大屏展示端连接：连接回放控制态，随后被动接收控制帧。"""

    async def connect(self):
        self.user = self.scope["user"]
        if not self.user:
            await self.close(4401)
            return
        self.pk = self.scope["url_route"]["kwargs"]["pk"]
        if not await database_sync_to_async(can_view_screen)(self.user, self.pk):
            await self.close(4403)
            return
        self.group_name = screen_group_name(self.pk)
        self.disconnected = False
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        # 回放当前控制态：后开/重连的展示端与最近一次指令对齐
        state = await database_sync_to_async(load_screen_state)(self.pk)
        await self.send_base_json(MessageAction.SCREEN_COMMAND.value, data={"command": "state", **state})

    async def disconnect(self, close_code):
        self.disconnected = True
        if getattr(self, "group_name", ""):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def ping(self, event):
        """心跳：沿用基类续期本连接所在组（组名不在个人推送组命名空间，不参与在线统计）。"""
        if getattr(self, "group_name", ""):
            await self.channel_layer.update_active_layers(self.group_name, self.channel_name)
        event["data"] = "pong"
        await self._send_base(event)

    async def screen_command(self, event):
        await self._send_base(event)
