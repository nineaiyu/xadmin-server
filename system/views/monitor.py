#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""系统监控面板：主机资源 / 服务健康 / Redis / Celery / 慢请求。

数据源全部复用既有基建（不引入新组件），采集逻辑在 system/utils/metrics.py
（与 WS 实时推送 ws_monitor.py 共用，保证两路口径一致）：
- 主机指标：common.Monitor 心跳表（startup 线程 30s 落盘，psutil 采集）+ 实时快照；
- 服务健康：common/utils/health.py（与 healthz 同一套探测，口径一致）；
- Redis：django_redis 连接池 client 的 INFO（缓存/渠道/broker 分库隔离，broker 库单独取队列长度）；
- Celery：inspect stats/active/reserved + broker 队列长度；
- 慢请求：OperationLog.exec_time（阈值 SysConfig.SLOW_REQUEST_THRESHOLD）。

HTTP 接口只读且短缓存（10s），权限走菜单 SystemMonitor（可授权给管理员角色），
不进 PERMISSION_WHITE_URL —— 监控数据属敏感信息；WS 通道（ws/system/monitor/）
同口径鉴权，前端可二选一或叠加使用。
"""

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.base.magic import cache_response
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.utils import metrics


class MonitorViewSet(GenericViewSet):
    """系统监控面板（只读）"""

    # 面板数据对实时性不敏感，短缓存避免多端同时刷新时重复采集
    dashboard_cache_timeout = 10

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f"{view_instance.__class__.__name__}_{view_method.__name__}"
        return f"{func_name}_{request.user.pk}"

    @extend_schema(responses=get_default_response_schema())
    @cache_response(timeout=dashboard_cache_timeout, key_func="get_cache_key")
    @action(methods=["get"], detail=False, url_path="overview")
    def overview(self, request, *args, **kwargs):
        """主机资源概览：实时快照 + 心跳最新值 + 最近趋势

        - live：直读 psutil（与心跳同源工具函数），卡片秒级新鲜；
        - latest/trend：common.Monitor 心跳表 30s 落盘，供趋势回看与兜底。
        """
        latest, trend = metrics.collect_latest_and_trend()
        return ApiResponse(
            data={
                "live": metrics.collect_live_metrics(),
                "latest": latest,
                "trend": trend,
            }
        )

    @extend_schema(responses=get_default_response_schema())
    @cache_response(timeout=dashboard_cache_timeout, key_func="get_cache_key")
    @action(methods=["get"], detail=False, url_path="services")
    def services(self, request, *args, **kwargs):
        """核心服务健康：DB / Redis / Celery 状态与探测耗时"""
        return ApiResponse(data=metrics.collect_services())

    @extend_schema(responses=get_default_response_schema())
    @cache_response(timeout=dashboard_cache_timeout, key_func="get_cache_key")
    @action(methods=["get"], detail=False, url_path="redis-info")
    def redis_info(self, request, *args, **kwargs):
        """缓存 Redis 实例关键指标（INFO 解析）与 Celery 队列长度"""
        return ApiResponse(data=metrics.collect_redis_info())

    @extend_schema(responses=get_default_response_schema())
    @cache_response(timeout=dashboard_cache_timeout, key_func="get_cache_key")
    @action(methods=["get"], detail=False, url_path="celery")
    def celery(self, request, *args, **kwargs):
        """Celery worker 与队列状态"""
        return ApiResponse(data=metrics.collect_celery_status())

    @extend_schema(responses=get_default_response_schema())
    @cache_response(timeout=dashboard_cache_timeout, key_func="get_cache_key")
    @action(methods=["get"], detail=False, url_path="slow")
    def slow(self, request, *args, **kwargs):
        """慢请求 Top N（最近窗口内 exec_time 超阈值的操作日志）"""
        return ApiResponse(data=metrics.collect_slow_requests())
