#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""动态表单视图（ADR-025）。

- DynamicFormViewSet（管理员）：表单定义 CRUD（schema 写入侧校验）；
- DynamicFormSubmissionViewSet：填报与本人提交管理——普通用户按 creator 隔离
  （只见/只改本人提交），超管全量；停用表单拒新提交（序列化器校验）。

定义/提交类资源不做行级数据权限过滤（与 ADR-020 Dataset 同款处理）。
"""

from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter

from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from system.models.dform import DynamicForm, DynamicFormSubmission
from system.serializers.dform import DynamicFormSerializer, DynamicFormSubmissionSerializer

_EDIT_DENY = _("Only the creator can modify a submission")


class DynamicFormViewSet(BaseModelSet):
    """动态表单定义（管理员）"""

    queryset = DynamicForm.objects.all()
    serializer_class = DynamicFormSerializer
    ordering = ["-created_time"]
    filter_backends = [DjangoFilterBackend, OrderingFilter]

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user, modifier=self.request.user)


class DynamicFormSubmissionViewSet(BaseModelSet):
    """动态表单提交（填报与本人提交管理；G5b 表单可挂审批流）"""

    queryset = DynamicFormSubmission.objects.select_related("form", "creator")
    serializer_class = DynamicFormSubmissionSerializer
    ordering = ["-created_time"]
    filter_backends = [DjangoFilterBackend, OrderingFilter]

    def create(self, request, *args, **kwargs):
        """提交：数据校验先行 → 审批门（approval_required 表单，G5b）→ 创建。

        审批协议与全局拦截器同构：412 待审批 → 审批人通过 → 申请人携
        X-Approval-Id 重放（服务端校验 creator/指纹/一次性），消费成功才落库。
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        form = serializer.validated_data["form"]

        if form.approval_required and not getattr(request.user, "is_superuser", False):
            from system.utils.approval import (
                APPROVAL_HEADER,
                APPROVAL_QUERY_PARAM,
                consume_approval,
                create_approval,
                find_active_pending,
                get_request_params,
                pending_response,
            )

            token = request.headers.get(APPROVAL_HEADER) or request.query_params.get(APPROVAL_QUERY_PARAM)
            if token:
                response = consume_approval(request, token)
                if response is not None:
                    return response
            else:
                approval = find_active_pending(request.user, request.method, request.path, get_request_params(request))
                if approval is None:
                    approval = create_approval(self, request)
                return pending_response(approval)

        self.perform_create(serializer)
        return ApiResponse(data=serializer.data)

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if getattr(user, "is_superuser", False):
            return queryset
        # creator 隔离：普通用户只见本人提交
        return queryset.filter(creator=user)

    def perform_create(self, serializer):
        serializer.save(creator=self.request.user, modifier=self.request.user)

    def _creator_guard(self, instance, request):
        if instance and not getattr(request.user, "is_superuser", False) and instance.creator_id != request.user.pk:
            return ApiResponse(code=1003, detail=_EDIT_DENY)
        return None

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        guarded = self._creator_guard(instance, request)
        if guarded:
            return guarded
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return ApiResponse(data=serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        guarded = self._creator_guard(instance, request)
        if guarded:
            return guarded
        return super().destroy(request, *args, **kwargs)
