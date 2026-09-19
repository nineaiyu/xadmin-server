#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""系统监控面板：主机资源 / 服务健康 / Redis / Celery / 指标历史 / 告警 / 事件 / 导出。

数据源全部复用既有基建（不引入新组件），采集逻辑在 system/utils/metrics.py
（与 WS 实时推送 ws_monitor.py 共用，保证两路口径一致）：
- 主机指标：common.Monitor 心跳表（startup 线程 30s 落盘，psutil 采集）+ 实时快照；
- 指标历史：monitor_history（时间范围/聚合粒度/多指标/环比，按需直查不缓存）；
- 服务健康：common/utils/health.py（与 healthz 同一套探测，口径一致）；
- 告警：monitor_events（MonitorAlert 记录）+ 阈值读写（settings.Setting 同源）；
- 事件：monitor_events（异常请求 / 任务失败明细）；
- 导出：monitor_export（CSV / Excel）。

HTTP 接口只读（阈值 PUT 除外）且短缓存（10s），权限走菜单 SystemMonitor
（可授权给管理员角色），不进 PERMISSION_WHITE_URL —— 监控数据属敏感信息；
WS 通道（ws/system/monitor/）同口径鉴权，前端可二选一或叠加使用。
"""

from django.conf import settings
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.base.magic import cache_response
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.utils import metrics, monitor_events, monitor_history
from system.utils.monitor_export import render_table_export

THRESHOLD_CATEGORY = "security_monitor"


class MonitorViewSet(GenericViewSet):
    """系统监控面板（只读 + 告警阈值设置）"""

    # 面板数据对实时性不敏感，短缓存避免多端同时刷新时重复采集
    dashboard_cache_timeout = 10

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f"{view_instance.__class__.__name__}_{view_method.__name__}"
        return f"{func_name}_{request.user.pk}"

    @extend_schema(responses=get_default_response_schema())
    @cache_response(timeout=dashboard_cache_timeout, key_func="get_cache_key")
    @action(methods=["get"], detail=False, url_path="overview")
    def overview(self, request, *args, **kwargs):
        """主机资源概览：实时快照 + 心跳最新值 + 最近趋势 + 健康总览

        - live：直读 psutil（与心跳同源工具函数），卡片秒级新鲜；
        - latest/trend：common.Monitor 心跳表 30s 落盘，供趋势回看与兜底；
        - health：健康分/分项状态/未恢复告警数（顶部总览横幅）。
        """
        latest, trend = metrics.collect_latest_and_trend()
        live = metrics.collect_live_metrics()
        services = metrics.collect_services()
        return ApiResponse(
            data={
                "live": live,
                "latest": latest,
                "trend": trend,
                "health": metrics.collect_health_summary(live=live, services=services),
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
    @action(methods=["get"], detail=False, url_path="task-health")
    def task_health(self, request, *args, **kwargs):
        """后台任务健康度（近 1 天聚合：成功率 / 健康色 / 高频任务 / 近期失败）"""
        return ApiResponse(data=metrics.collect_task_health())

    @extend_schema(responses=get_default_response_schema())
    @cache_response(timeout=dashboard_cache_timeout, key_func="get_cache_key")
    @action(methods=["get"], detail=False, url_path="slow")
    def slow(self, request, *args, **kwargs):
        """慢请求 Top N（最近窗口内 exec_time 超阈值的操作日志）"""
        return ApiResponse(data=metrics.collect_slow_requests())

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="history")
    def history(self, request, *args, **kwargs):
        """指标历史趋势：时间范围（range=1h/6h/24h/7d/30d 或 start/end）、

        聚合粒度（interval=auto/1m/5m/15m/1h/1d）、多指标（metrics 逗号分隔）
        与环比对比。查询参数多，不套短缓存（避免不同窗口互相污染）。
        """
        result = monitor_history.collect_history(
            range_key=request.query_params.get("range"),
            start=request.query_params.get("start"),
            end=request.query_params.get("end"),
            interval=request.query_params.get("interval"),
            metrics=request.query_params.get("metrics"),
            compare=request.query_params.get("compare") not in ("0", "false"),
        )
        return ApiResponse(data=result)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get", "put"], detail=False, url_path="thresholds")
    def thresholds(self, request, *args, **kwargs):
        """资源告警阈值：GET 读取 / PUT 更新（与安全设置同源 Setting 表）"""
        from settings.serializers.security import SecurityMonitorSerializer

        if request.method == "PUT":
            serializer = SecurityMonitorSerializer(data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            changed = self.persist_thresholds(serializer, request)
            payload = self.thresholds_payload()
            payload["changed"] = changed
            return ApiResponse(data=payload)
        return ApiResponse(data=self.thresholds_payload())

    @staticmethod
    def thresholds_payload():
        """当前阈值与表单元数据（label/范围来自 SecurityMonitorSerializer 单源）。"""
        from settings.serializers.security import SecurityMonitorSerializer

        items = []
        for name, field in SecurityMonitorSerializer().get_fields().items():
            items.append(
                {
                    "key": name,
                    "value": getattr(settings, name, None),
                    "label": str(field.label),
                    "help_text": str(field.help_text),
                    "min": field.min_value,
                    "max": field.max_value,
                }
            )
        return {"items": items, "check_interval_seconds": 60}

    @staticmethod
    def persist_thresholds(serializer, request):
        """写 Setting 并同步本进程 settings（其他进程由 pubsub 回写）。"""
        from settings.models import Setting

        changed = []
        for name, value in serializer.validated_data.items():
            is_changed, setting = Setting.update_or_create(
                name=name, value=value, encrypted=False, category=THRESHOLD_CATEGORY, user=request.user
            )
            if is_changed:
                changed.append(name)
                setting.refresh_setting()
        return changed

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="events")
    def events(self, request, *args, **kwargs):
        """事件记录查询：kind=alert（告警）/ error（异常请求）/ task（任务失败）"""
        data = monitor_events.collect_events(
            kind=request.query_params.get("kind") or "alert",
            range_key=request.query_params.get("range") or "24h",
            status=request.query_params.get("status") or None,
            item=request.query_params.get("item") or None,
        )
        return ApiResponse(data=data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="export")
    def export(self, request, *args, **kwargs):
        """报表导出：kind=history（趋势数据+汇总）/ alerts（告警记录），type=csv|xlsx"""
        file_format = "xlsx" if request.query_params.get("type") == "xlsx" else "csv"
        if request.query_params.get("kind") == "alerts":
            alerts = monitor_events.collect_alerts(
                status=request.query_params.get("status") or None,
                item=request.query_params.get("item") or None,
                range_key=request.query_params.get("range") or "30d",
                limit=1000,
            )
            sheets = monitor_events.build_alert_export_sheets(alerts["results"])
            prefix = "monitor-alerts"
        else:
            result = monitor_history.collect_history(
                range_key=request.query_params.get("range"),
                start=request.query_params.get("start"),
                end=request.query_params.get("end"),
                interval=request.query_params.get("interval"),
                metrics=request.query_params.get("metrics"),
            )
            sheets = monitor_history.build_history_export_sheets(result)
            prefix = "monitor-history"
        filename, content, content_type = render_table_export(prefix, sheets, file_format)
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
