#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""报表设计版本领域服务：快照、保留数裁剪。"""

from typing import Any

from django.conf import settings

from dataset.models.report import Report, ReportVersion

#: 版本保留条数（回滚窗口；超出丢弃最旧，避免版本表无界增长）
REPORT_VERSION_KEEP = 20


def version_keep() -> int:
    """版本保留数：允许 settings 覆盖（便于按环境/测试调小），缺省取领域常量。"""
    return int(getattr(settings, "REPORT_VERSION_KEEP", REPORT_VERSION_KEEP))


def snapshot_report_version(report: Any, actor: Any = None) -> Any:
    """给报表记一版设计快照，并把超出保留数的旧版本裁掉。

    版本号沿用「当前最大 + 1」；``design_version`` 与版本表同步（只改主表字段，
    不动 ``updated_time``——乐观锁基线不应因版本记账而前进）。
    """
    latest = report.versions.first()
    version = (latest.version if latest else 0) + 1
    snapshot = ReportVersion.objects.create(
        report=report,
        version=version,
        design=report.design or {},
        creator=actor,
        modifier=actor,
    )
    Report.objects.filter(pk=report.pk).update(design_version=version)
    report.design_version = version
    stale = list(report.versions.values_list("pk", flat=True)[version_keep() :])
    if stale:
        ReportVersion.objects.filter(pk__in=stale).delete()
    return snapshot
