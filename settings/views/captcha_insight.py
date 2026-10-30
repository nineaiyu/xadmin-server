#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""验证码观测面视图（日志列表 + 大盘聚合，均只读）。

模型与聚合逻辑在 captcha 域，经 ``captcha.contracts`` 唯一缝再导出；
本模块只做安全域页面归属的视图装配。两个端点都不提供任何写操作——
观测数据是审计痕迹，不可经 API 抹除。
"""

from typing import Any

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.viewsets import GenericViewSet

from captcha.contracts import (
    CaptchaLog,
    CaptchaLogFilter,
    CaptchaLogSerializer,
    CaptchaStatsInSerializer,
    CaptchaStatsResponseSerializer,
    captcha_stats,
)
from common.core.modelset import OnlyListModelSet
from common.core.response import ApiResponse


class SecurityCaptchaLogViewSet(OnlyListModelSet):
    """验证码观测日志"""

    queryset = CaptchaLog.objects.all()
    serializer_class = CaptchaLogSerializer
    filterset_class = CaptchaLogFilter
    ordering_fields = ["ts"]


class SecurityCaptchaStatsViewSet(GenericViewSet):
    """验证码数据大盘

    聚合口径：时间窗内按 phase 汇总、按题型 / 决策分组、按小时分桶；
    数据源 CaptchaLog 只含加盐哈希与 UA 大类，无个体可还原字段。
    """

    serializer_class = CaptchaStatsInSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="hours",
                type=int,
                location=OpenApiParameter.QUERY,
                description="Aggregation window in hours (1-168, default 24)",
            )
        ],
        responses={200: OpenApiResponse(response=CaptchaStatsResponseSerializer)},
        request=None,
    )
    def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """读取时间窗聚合"""
        serializer = self.get_serializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        hours = int(serializer.validated_data.get("hours") or 24)
        return ApiResponse(data=captcha_stats(hours))
