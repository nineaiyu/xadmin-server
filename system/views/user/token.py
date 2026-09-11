#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""个人访问令牌（PAT）管理视图：个人凭证个人管。

- 取值域严格个人：任何用户（含超管）只见本人凭证，无管理员代管（登记边界）；
- 路由挂在 PERMISSION_WHITE_URL（个人安全操作，无需菜单权限，同 MFA 口径），
  但仍需登录（认证链生效）。
- 调用审计不新增埋点表：OperationLog 记录 PAT 请求时写入凭证标识（auth_type=pat +
  token_pk，见 common/core/middleware.py），logs/stats 按 token_pk 精确归集；
  升级前（无 token_pk）的历史日志无法归属到具体凭证，不计入并在前端标注。
"""

from django.utils import timezone
from django_filters import rest_framework as dj_filters
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models.token import PersonalAccessToken
from system.serializers.log import OperationLogSerializer
from system.serializers.token import PersonalAccessTokenSerializer

# 调用统计回看窗口（近 7 天）
PAT_STATS_WINDOW_DAYS = 7
# 业务成功码（ApiResponse 约定）：status_code != 1000 记为失败调用
API_SUCCESS_CODE = 1000


class PersonalAccessTokenFilter(dj_filters.FilterSet):
    """列表过滤：名称/前缀模糊 + 启用状态精确 + 创建时间范围。

    个人取值域由 get_queryset 收口为本人，过滤只做展示层收敛；
    search-fields 元数据由该 FilterSet 自动派生。
    """

    name = dj_filters.CharFilter(field_name="name", lookup_expr="icontains")
    token_prefix = dj_filters.CharFilter(field_name="token_prefix", lookup_expr="icontains")
    created_time = dj_filters.DateTimeFromToRangeFilter()

    class Meta:
        model = PersonalAccessToken
        fields = ["name", "token_prefix", "is_active", "created_time"]


class PersonalAccessTokenViewSet(BaseModelSet):
    """个人访问令牌"""

    queryset = PersonalAccessToken.objects.all()
    serializer_class = PersonalAccessTokenSerializer
    filterset_class = PersonalAccessTokenFilter
    # PAT 是个人凭证：剥离默认数据权限过滤（默认拒绝会让本人凭证不可见），
    # 仅保留字段过滤与排序；取值域由 get_queryset 收口为本人
    filter_backends = (dj_filters.DjangoFilterBackend, OrderingFilter)
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "last_used_time", "expired_at"]

    def get_queryset(self):
        # 严格个人取值域：含超管在内都只看本人凭证
        return self.queryset.filter(creator=self.request.user)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="logs")
    def logs(self, request, *args, **kwargs):
        """凭证调用记录（精确口径：按凭证标识 token_pk 归集）"""
        token = self.get_object()  # 取值域保护：他人凭证 404
        queryset = self._call_log_queryset(request, token)
        page = self.paginate_queryset(queryset)
        if page is not None:
            data = self.get_paginated_response(OperationLogSerializer(page, many=True).data).data
        else:
            data = {"total": queryset.count(), "results": OperationLogSerializer(queryset, many=True).data}
        return ApiResponse(data=data)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=True, url_path="stats")
    def stats(self, request, *args, **kwargs):
        """调用统计（近 7 天调用数 / 失败数 / 末次调用时间，按凭证精确归集）"""
        token = self.get_object()
        queryset = self._call_log_queryset(
            request, token, since=timezone.now() - timezone.timedelta(days=PAT_STATS_WINDOW_DAYS)
        )
        total = queryset.count()
        failed = queryset.exclude(status_code=API_SUCCESS_CODE).count()
        return ApiResponse(
            data={
                "total": total,
                "failed": failed,
                "window_days": PAT_STATS_WINDOW_DAYS,
                "last_used_time": token.last_used_time,
            }
        )

    @staticmethod
    def _call_log_queryset(request, token, since=None):
        """凭证调用日志按时间窗/路径过滤（精确口径：token_pk 命中即该凭证的调用）。

        只按 token_pk 过滤：凭证与日志归属同一属主，且 token_pk 唯一定位凭证；
        token_pk 为空的历史行（升级前写入）天然不计入任一凭证。
        """
        from system.models import OperationLog

        queryset = OperationLog.objects.filter(token_pk=token.pk)
        params = request.query_params
        if since is not None:
            queryset = queryset.filter(created_time__gte=since)
        if params.get("start"):
            queryset = queryset.filter(created_time__gte=params["start"])
        if params.get("end"):
            queryset = queryset.filter(created_time__lte=params["end"])
        if params.get("path"):
            queryset = queryset.filter(path__icontains=params["path"])
        return queryset.order_by("-created_time")
