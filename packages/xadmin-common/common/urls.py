#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from django.db import transaction
from django.urls import re_path

from common.api.backup import BackupAlertAPIView
from common.api.common import CountryListAPIView, HealthCheckAPIView, ResourcesIDCacheAPIView
from common.api.csp import CSPReportAPIView
from common.api.metrics import MetricsAPIView
from common.api.ops_alert import OpsAlertAPIView

app_name = "common"

urlpatterns = [
    re_path("^resources/cache$", ResourcesIDCacheAPIView.as_view(), name="resources-cache"),
    re_path("^countries$", CountryListAPIView.as_view(), name="countries"),
    # 基础设施端点豁免请求级事务（ATOMIC_REQUESTS=True）：health / metrics / csp-report
    # 不依赖数据库——DB 故障时 ATOMIC_REQUESTS 会在进入视图前 ensure_connection 并直接 500
    # （2026-09-18 真丢包演练定位：health 在 DB 半开/池超时时被请求入口打成 500，修复前
    # 还会挂到 TCP 重传耗竭）。用 Django 官方 non_atomic_requests 包装 URLconf callback。
    re_path("^api/health", transaction.non_atomic_requests(HealthCheckAPIView.as_view()), name="health"),
    re_path("^api/metrics", transaction.non_atomic_requests(MetricsAPIView.as_view()), name="metrics"),
    # 备份失败告警上报（S2）：独立令牌鉴权，供 db-backup 容器回调
    re_path("^api/backup-alert$", BackupAlertAPIView.as_view(), name="backup-alert"),
    # 运维告警上报（A1）：独立令牌鉴权，供宿主侧 watcher（容器 OOM 等）回调
    re_path("^api/ops-alert$", OpsAlertAPIView.as_view(), name="ops-alert"),
    # CSP 违规上报（S3 观察期）：浏览器 report-uri 目标，只记日志不落库
    re_path("^api/csp-report$", transaction.non_atomic_requests(CSPReportAPIView.as_view()), name="csp-report"),
]
