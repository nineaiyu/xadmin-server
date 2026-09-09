#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""监控面板 WebSocket 实时推送。

前端连接 ws/system/monitor/ 后，服务端分两档频率推送指标（section 区分载荷）：
- live：主机资源 psutil 快照，5s 一推（轻量，仪表盘卡片秒级新鲜）；
- panel：服务健康 / Redis / Celery / 慢请求 / 趋势，30s 一推（重采集），
  连接建立时立即推一帧兜底。

与 HTTP action（system/views/monitor.py + system/utils/metrics.py）共用同一套
采集函数，保证 WS 与 HTTP 两路口径一致。采集为阻塞调用，统一经
database_sync_to_async 丢线程池执行，避免拖慢共享事件循环。

鉴权与 HTTP 同口径：
- 未登录 4401（asgi Cookie 认证中间件，scope['user']）；
- 超管或持有 SystemMonitor 菜单权限（api/system/monitor/overview GET）才可
  订阅，否则 4403 —— 监控数据属敏感信息，不进 PERMISSION_WHITE_URL。
"""

import asyncio

from channels.db import database_sync_to_async

from common.utils import get_logger
from message.base import AsyncJsonWebsocket
from message.protocol import MessageAction
from system.utils import metrics

logger = get_logger(__name__)

LIVE_PUSH_INTERVAL = 5
PANEL_PUSH_EVERY = 6  # 每 6 个 live 周期推一次 panel（约 30s）
MONITOR_PERMISSION_KEY = "api/system/monitor/overview$"


@database_sync_to_async
def _has_monitor_permission(user) -> bool:
    """超管全量；普通用户需持有 SystemMonitor 的 list 菜单权限（与 HTTP 同口径）。"""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    from common.core.permission import get_user_permission

    try:
        return MONITOR_PERMISSION_KEY in get_user_permission(user, "GET")
    except Exception:  # noqa: BLE001 权限读取失败 fail-closed
        logger.warning("monitor ws permission check failed", exc_info=True)
        return False


@database_sync_to_async
def _collect_panel():
    """重采集集中在线程池执行（celery inspect 广播最长阻塞 ~1s，不能占事件循环）。"""
    return {
        "section": "panel",
        "services": metrics.collect_services(),
        "redis": metrics.collect_redis_info(),
        "celery": metrics.collect_celery_status(),
        "slow": metrics.collect_slow_requests(),
        "trend": metrics.collect_latest_and_trend()[1],
    }


class MonitorNotify(AsyncJsonWebsocket):
    """一条连接持续推送面板指标；断开即停，无分组广播（面板数据按连接隔离推送）。"""

    async def connect(self):
        self.user = self.scope["user"]
        if not self.user:
            await self.close(4401)
            return
        if not await _has_monitor_permission(self.user):
            await self.close(4403)
            return
        self.disconnected = False
        await self.accept()
        asyncio.create_task(self.push_loop())

    async def disconnect(self, close_code):
        self.disconnected = True

    async def ping(self, event):
        """监控连接不属于消息层分组（无 group_name），心跳静默忽略即可；
        沿用基类实现会因缺少 group_name 抛 AttributeError 断连（同 TaskLogNotify）。"""

    async def push_loop(self):
        try:
            tick = 0
            while not self.disconnected:
                # live 轻量（psutil 纯 CPU 计算，无 IO），可直接在事件循环执行
                await self.send_base_json(
                    MessageAction.MONITOR.value,
                    {"section": "live", "live": metrics.collect_live_metrics()},
                )
                if tick % PANEL_PUSH_EVERY == 0:
                    await self.send_base_json(MessageAction.MONITOR.value, await _collect_panel())
                tick += 1
                await asyncio.sleep(LIVE_PUSH_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Monitor push failed")
        finally:
            if not self.disconnected:
                await self.close()
