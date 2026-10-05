#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 观测端点（自 assistant.py 拆分，URL 路径 / 权限点 / 行为不变）。

含 usage（用量账本汇总：按天 / 按链路 / Top 用户 + 配额与并发占用）与
metrics（AI 调用观测：近 N 天用量 / 成功率 / 日趋势 / 类型分布 / Top 用户）。
两个 action 与 status 共用同一权限点路径正则，不新增权限点。
"""

from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema


class AiObservabilityMixin:
    """AI 用量 / 观测 action（self 由组合它的 ViewSet 提供）。"""

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="usage")
    def usage(self, request, *args, **kwargs):
        """AI 用量账本：按天 / 按链路 / Top 用户 + 配额配置与并发占用。

        数据源 = AiUsageRecord（逐次记账，保留期随 MONITOR_RETENTION_DAYS 清理）；
        与 status/metrics 共用权限点路径正则（`(status|metrics|history|tools|usage)$`），
        不新增权限点。
        """
        from ai.utils.ai_usage import usage_summary

        days = request.query_params.get("days") or 7
        feature = str(request.query_params.get("feature") or "").strip()
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 7
        return ApiResponse(data=usage_summary(days=days, feature=feature))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="metrics")
    def metrics(self, request, *args, **kwargs):
        """AI 调用观测：近 N 天用量 / 成功率 / 日趋势 / 类型分布 / Top 用户。

        数据源 = OperationLog(auth_type=ai)：AI:ask（文档问答）/ AI:nl_query（NL 查数）/
        AI:action（受限动作）；权限点与 status 共用同一路径正则（`(status|metrics)$`，
        见菜单种子），不新增权限点。
        """
        from datetime import timedelta

        from django.db.models import Count
        from django.db.models.functions import TruncDate
        from django.utils import timezone

        from audit.services import OperationLog
        from identity.models import UserInfo

        try:
            days = int(request.query_params.get("days") or 30)
        except (TypeError, ValueError):
            days = 30
        days = max(1, min(days, 90))
        since = (timezone.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

        base = OperationLog.objects.filter(
            auth_type=OperationLog.AuthType.AI,
            created_time__gte=since,
        )
        total = base.count()
        failed = base.exclude(status_code=1000).count()

        module_labels = {"AI:ask": "文档问答", "AI:nl_query": "NL 查数", "AI:action": "受限动作"}
        by_module = [
            {
                "module": row["module"] or "",
                "label": module_labels.get(row["module"] or "", row["module"] or "未知"),
                "count": row["count"],
            }
            for row in base.values("module").annotate(count=Count("id")).order_by("-count")
        ]
        by_day = [
            {"date": row["day"].isoformat(), "module": row["module"] or "", "count": row["count"]}
            for row in base.annotate(day=TruncDate("created_time"))
            .values("day", "module")
            .annotate(count=Count("id"))
            .order_by("day")
        ]
        top_rows = (
            base.exclude(object_pk__isnull=True)
            .exclude(object_pk="")
            .values("object_pk")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        name_map = {
            str(pk): username
            for pk, username in UserInfo.objects.filter(pk__in=[row["object_pk"] for row in top_rows]).values_list(
                "pk", "username"
            )
        }
        # token 用量（成本维度）：DB 侧聚合 AI 用量账本（不再逐行解析审计 changes JSON）
        from ai.utils.ai_usage import usage_tokens_since

        return ApiResponse(
            data={
                "days": days,
                "total": total,
                "success": total - failed,
                "failed": failed,
                "by_module": by_module,
                "by_day": by_day,
                "top_users": [
                    {"username": name_map.get(row["object_pk"], row["object_pk"][:12]), "count": row["count"]}
                    for row in top_rows
                ],
                "tokens": usage_tokens_since(since),
            }
        )
