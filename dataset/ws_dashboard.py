#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""仪表盘（工作台）数据推送：WebSocket 下行通道。

- 观察端连接 `ws/dashboard/<pk>`：准入与 HTTP 可见性同口径（超管 / 创建者 /
  shared）；连接即自推一帧当前数据（观察者无需等下一个推送周期）；
- 服务端不做组广播数据：数据权限绑定浏览者，触发事件到达各连接后以**连接自身
  用户**视角聚合（dataset/dashboard_data.py）再只发给自己，与逐卡 HTTP 重拉
  权限语义等价（M 卡 × N 观察者收敛为每观察者每轮 1 帧）；
- 观察端基本被动接收：无上行数据动作（未知上行动作回执 1001）；
- 触发源：beat 周期任务 push_dashboard_data（analysis_tasks，固定下限节流）；
  连接侧按合并窗口合并背靠背的重复触发。
"""

import time
from typing import Any

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from dataset.dashboard_data import build_dashboard_data_payload, load_visible_dashboard
from message.ws.base import AsyncJsonWebsocket
from message.ws.protocol import MessageAction

logger = get_logger(__name__)

# beat 推送节流的下限（秒）：与大屏 MIN_DATA_PUSH_INTERVAL 同口径
MIN_DATA_PUSH_INTERVAL = 10
# last_push 时间戳的缓存 TTL（节流预筛 + 观测）
DATA_PUSH_TS_TTL = 3600 * 2
# 连接侧触发合并窗口（秒）：beat 触发背靠背到达时，窗口内的重复触发只聚合一次
TRIGGER_MERGE_WINDOW = 1.0


def dashboard_group_name(dashboard_pk: Any) -> str:
    return f"dashboard_data_{dashboard_pk}"


def dashboard_push_ts_key(dashboard_pk: Any) -> str:
    """beat 数据推送节流键（last_push epoch 秒）。"""
    return f"dashboard_data_push_{dashboard_pk}"


def dashboard_push_claim_key(dashboard_pk: Any) -> str:
    """beat 推送原子占用键：``cache.add`` 占位即赢得本轮推送权。"""
    return f"dashboard_data_push_claim_{dashboard_pk}"


def broadcast_dashboard_data_trigger(dashboard_pk: Any) -> None:
    """请求该仪表盘的所有在线观察连接各自聚合并自推一帧数据（离盘时静默丢弃）。

    只投递无载荷触发事件：数据聚合在各连接内以观察者自身权限执行，见模块 docstring。
    """
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(dashboard_group_name(dashboard_pk), {"type": "dashboard_data_trigger"})


def dashboard_push_due(dashboard_pk: Any, now: Any = None) -> bool:
    """beat 节流判定：距上次推送不足推送下限（10s）则跳过。"""
    now = int(now if now is not None else time.time())
    last = cache.get(dashboard_push_ts_key(dashboard_pk))
    if last:
        try:
            if now - int(last) < MIN_DATA_PUSH_INTERVAL:
                return False
        except (TypeError, ValueError):
            pass  # 脏值视同未推送，交由本轮重置
    return True


def mark_dashboard_pushed(dashboard_pk: Any, now: Any = None) -> None:
    """记录本轮推送时刻（离盘不落键：观察者上线后能尽快收到首帧）。"""
    cache.set(dashboard_push_ts_key(dashboard_pk), int(now if now is not None else time.time()), DATA_PUSH_TS_TTL)


def claim_dashboard_push(dashboard_pk: Any, now: Any = None) -> bool:
    """原子占位一轮 beat 推送：并发扫描只有一个赢者（``cache.add`` 单命令语义）。

    与大屏 claim_screen_push 同款：占位键 TTL = 推送下限，到期自动放行下一轮。
    """
    claimed: bool = cache.add(
        dashboard_push_claim_key(dashboard_pk),
        int(now if now is not None else time.time()),
        MIN_DATA_PUSH_INTERVAL,
    )
    return claimed


class DashboardDataNotify(AsyncJsonWebsocket):
    """仪表盘观察端连接：连接即自推当前数据帧，随后被动接收数据触发事件。"""

    disconnected = False  # 已断开标记（disconnect 后不再续期所在组）
    # 上次聚合完成时刻（monotonic）：触发合并窗口的判定基准
    _last_built_at: float = 0.0

    async def connect(self) -> None:
        self.user = self.scope["user"]
        if not self.user:
            await self.close(4401)
            return
        self.pk = self.scope["url_route"]["kwargs"]["pk"]
        if not await database_sync_to_async(load_visible_dashboard)(self.user, self.pk):
            await self.close(4403)
            return
        self.group_name = dashboard_group_name(self.pk)
        self.disconnected = False
        self._last_built_at = 0.0
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        # 连接即推首帧：观察者无需等下一个 beat 周期（失败帧由 trigger 兜底重试）
        await self._build_and_send()

    async def disconnect(self, close_code: Any) -> None:
        self.disconnected = True
        if getattr(self, "group_name", ""):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, action: Any, data: Any, content: Any, **kwargs: Any) -> None:
        """上行通道：观察端无数据上行动作，未知动作回执 1001（与大屏展示端同语义）。"""
        await self.send_base_json(action, code=1001, detail=_("Unknown action for dashboard channel"))

    async def ping(self, event: Any) -> None:
        """心跳：沿用基类续期本连接所在组（组名不在个人推送组命名空间，不参与在线统计）。"""
        if getattr(self, "group_name", ""):
            await self.channel_layer.update_active_layers(self.group_name, self.channel_name)
        event["data"] = "pong"
        await self._send_base(event)

    async def dashboard_data_trigger(self, event: Any) -> None:
        """数据触发事件（组广播、无载荷）：以本连接的观察者视角聚合整盘并只发给自己。

        触发合并：beat 周期推送可能背靠背投递触发事件，同一连接虽逐事件串行处理，
        仍会在合并窗口内反复整盘聚合——距上次聚合完成不足窗口时跳过本轮
        （数据是否变化由「每轮重新聚合」保证，窗口只合并真正的重复事件）。
        """
        # 断开后仍可能收到排队中的触发事件：丢弃，不再向已死连接写帧
        if getattr(self, "disconnected", False):
            return
        if time.monotonic() - self._last_built_at < TRIGGER_MERGE_WINDOW:
            return
        await self._build_and_send()

    async def _build_and_send(self) -> None:
        """聚合整盘数据帧并自推（可见性兜底 + 失败降级为全盘级错误帧）。"""
        pk = getattr(self, "pk", "")
        # 可见性兜底（connect 已把关）：盘被删 / 共享被收回时静默跳过
        dashboard = await database_sync_to_async(load_visible_dashboard)(self.user, pk)
        if dashboard is None:
            return
        try:
            payload = await database_sync_to_async(build_dashboard_data_payload)(self.user, dashboard)
        except Exception:  # noqa: BLE001 构建失败不让连接死：降级为全盘级错误帧（单卡报错同语义）
            logger.warning("dashboard data build failed: dashboard=%s", pk, exc_info=True)
            payload = {
                "dashboard": str(pk),
                "cards": [],
                "errors": [{"card": "*", "detail": _("Dashboard data build failed")}],
                "ts": int(time.time()),
            }
        else:
            # 聚合成功才推进基准：失败帧的快速重试不被合并窗口吞掉
            self._last_built_at = time.monotonic()
        await self.send_base_json(MessageAction.DASHBOARD_DATA.value, data=payload)
