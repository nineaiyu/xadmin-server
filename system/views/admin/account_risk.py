#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""账号安全风险巡检（F-6）：清单 / 立即巡检 / 逐项处置 / 批量处置 / 汇总。

风险项由巡检任务产出（不提供手工创建），因此视图集只暴露列表 + 处置动作。
"""

from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action

from common.core.filter import BaseFilterSet
from common.core.modelset import OnlyListModelSet
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models import AccountRisk
from system.serializers.security import AccountRiskSerializer
from system.utils.account_risk import HANDLE_ACTIONS, handle_account_risk, scan_account_risks


class AccountRiskFilter(BaseFilterSet):
    class Meta:
        model = AccountRisk
        fields = ["risk_type", "level", "status", "user"]


class AccountRiskViewSet(OnlyListModelSet):
    """账号安全风险项"""

    queryset = AccountRisk.objects.select_related("user", "handled_by").all()
    serializer_class = AccountRiskSerializer
    filterset_class = AccountRiskFilter
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "updated_time", "level", "handled_at"]

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="scan")
    def scan(self, request, *args, **kwargs):
        """立即执行一次账号安全巡检"""
        result = scan_account_risks(operator=request.user)
        return ApiResponse(data=result, detail=_("Scan completed: {} risk item(s)").format(result["total"]))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "action": build_basic_type(OpenApiTypes.STR),
                    "remark": build_basic_type(OpenApiTypes.STR),
                },
                required=["action"],
                description="处置动作：{}".format("/".join(HANDLE_ACTIONS)),
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=True, url_path="handle")
    def handle(self, request, *args, **kwargs):
        """处置风险项"""
        risk = self.get_object()
        ok, message = handle_account_risk(
            risk, request.data.get("action"), operator=request.user, remark=request.data.get("remark") or ""
        )
        if not ok:
            return ApiResponse(code=1001, detail=message)
        return ApiResponse(data=self.get_serializer(risk).data, detail=_("Handled successfully"))

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR)),
                    "action": build_basic_type(OpenApiTypes.STR),
                    "remark": build_basic_type(OpenApiTypes.STR),
                },
                required=["pks", "action"],
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-handle")
    def batch_handle(self, request, *args, **kwargs):
        """批量处置风险项（逐项隔离，返回成功 / 失败明细）"""
        pks = request.data.get("pks") or []
        if not isinstance(pks, (list, tuple)) or not pks:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        action_name = request.data.get("action")
        remark = request.data.get("remark") or ""
        queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=pks)
        success, failures = [], []
        for risk in queryset:
            ok, message = handle_account_risk(risk, action_name, operator=request.user, remark=remark)
            if ok:
                success.append(str(risk.pk))
            else:
                failures.append({"pk": str(risk.pk), "reason": message})
        return ApiResponse(
            data={"success": success, "failures": failures},
            detail=_("Handled: {} succeeded, {} failed").format(len(success), len(failures)),
        )

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False)
    def stats(self, request, *args, **kwargs):
        """风险汇总（按等级 / 状态 / 类型）"""
        base = self.filter_queryset(self.get_queryset())
        by_level = {item["level"]: item["count"] for item in base.values("level").annotate(count=Count("pk"))}
        by_status = {item["status"]: item["count"] for item in base.values("status").annotate(count=Count("pk"))}
        by_type = list(
            base.values("risk_type").annotate(count=Count("pk")).order_by("-count").values("risk_type", "count")
        )
        return ApiResponse(
            data={
                "total": base.count(),
                "pending": by_status.get(AccountRisk.Status.PENDING, 0),
                "by_level": by_level,
                "by_status": by_status,
                "by_type": by_type,
                "actions": list(HANDLE_ACTIONS),
            }
        )
