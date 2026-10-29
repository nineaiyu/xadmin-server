#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""审批实例的批量动作端点（自 approval_instance_actions.py 拆分，URL 路径 / 权限点 / 行为不变）。

包含：批量通过 / 批量驳回 / 批量转交。ViewSet 经 ApprovalInstanceActionMixin 组合本
mixin 后对外行为与拆分前完全一致。
"""

import uuid
from typing import TYPE_CHECKING, Any

from django.db.models import Prefetch
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from approval.models.approval import ApprovalNodeTask
from approval.services.approval_flow import approve_task, reject_task, transfer_task, visible_instances_for
from approval.utils.approval_mfa import ensure_approval_action_confirmed
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema

MAX_BATCH_INSTANCES = 200


def normalize_uuid_pks(pks: Any) -> tuple[list[str], list[dict[str, str]]]:
    """批量实例主键归一：UUID 解析失败逐项进 failures，避免 filter(pk__in) 抛 500。"""
    if not isinstance(pks, (list, tuple)):
        return [], [{"pk": str(pks), "detail": str(_("Invalid application id"))}]
    valid: list[str] = []
    failures: list[dict[str, str]] = []
    for value in pks:
        try:
            parsed = str(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            failures.append({"pk": str(value), "detail": str(_("Invalid application id"))})
            continue
        if parsed not in valid:
            valid.append(parsed)
    return valid, failures


def missing_pk_failures(pks: Any, handled_pks: Any) -> list[Any]:
    """取值域外/已失效 pk 的失败明细（不可见单不泄露存在性，原因统一口径）。

    批量入口只对取值域内可见的单逐条处理；勾选中其余 pk 若静默跳过，
    前端「勾选 N 条」与结果数就对不上——统一计入 failures 明细返回。
    """
    detail = str(_("No visible application for the given id"))
    return [{"pk": str(pk), "detail": detail} for pk in pks if str(pk) not in handled_pks]


class ApprovalInstanceBatchMixin:
    """实例批量动作端点（self 由组合它的 ViewSet 提供：get_object / filter_queryset 等）。"""

    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）

        def get_object(self, *args: Any, **kwargs: Any) -> Any: ...

        def get_queryset(self, *args: Any, **kwargs: Any) -> Any: ...

        def filter_queryset(self, queryset: Any, *args: Any, **kwargs: Any) -> Any: ...

    def _with_my_pending_tasks(self, queryset: Any, user: Any) -> Any:
        """批量子查询预取「我的待办」：N 条实例只发一条任务查询（逐条过滤会 N+1）。"""
        return queryset.prefetch_related(
            Prefetch(
                "tasks",
                queryset=ApprovalNodeTask.objects.filter(assignee=user, status=ApprovalNodeTask.Status.PENDING),
                to_attr="_my_pending_tasks",
            )
        )

    @staticmethod
    def _prefetched_my_task(instance: Any) -> Any:
        return next(
            (task for task in instance._my_pending_tasks if task.node_id == instance.current_node_id),
            None,
        )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                    "comment": build_basic_type(OpenApiTypes.STR),
                },
                required=["pks"],
                description="主键列表 + 审批意见",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-approve")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_approve(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """批量通过

        逐个定位当前用户的待办任务，返回成功数与被拒明细
        """
        ensure_approval_action_confirmed(request, "batch_approve")
        pks = request.data.get("pks") or []
        if not isinstance(pks, (list, tuple)) or not pks:
            raise ValidationError(_("Please select the data to operate"))
        if len(pks) > MAX_BATCH_INSTANCES:
            raise ValidationError(_("Too many applications in one batch (max {})").format(MAX_BATCH_INSTANCES))
        comment = (request.data.get("comment") or "").strip()
        valid_pks, failures = normalize_uuid_pks(pks)
        succeeded, handled_pks = 0, set()
        queryset = self._with_my_pending_tasks(
            self.filter_queryset(self.get_queryset()).filter(pk__in=valid_pks), request.user
        )
        for instance in queryset:
            handled_pks.add(str(instance.pk))
            task = self._prefetched_my_task(instance)
            if task is None:
                failures.append({"pk": str(instance.pk), "detail": str(_("No pending task for you"))})
                continue
            ok, detail = approve_task(task.pk, request.user, comment)
            if ok:
                succeeded += 1
            else:
                failures.append({"pk": str(instance.pk), "detail": str(detail)})
        # 只对通过 UUID 归一的主键补「不可见」明细：非法值已在归一阶段进 failures，
        # 再按原 pks 遍历会给同一个非法值追加第二条失败记录（勾选数与明细对不上）
        failures.extend(missing_pk_failures(valid_pks, handled_pks))
        return ApiResponse(
            data={"succeeded": succeeded, "failures": failures},
            detail=_("Operation successful. Approved {} data").format(succeeded),
        )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                    "reason": build_basic_type(OpenApiTypes.STR),
                },
                required=["pks", "reason"],
                description="主键列表 + 驳回原因",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-reject")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_reject(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """批量驳回（原因必填）"""
        ensure_approval_action_confirmed(request, "batch_reject")
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError(_("Rejection reason is required"))
        pks = request.data.get("pks") or []
        if not isinstance(pks, (list, tuple)) or not pks:
            raise ValidationError(_("Please select the data to operate"))
        if len(pks) > MAX_BATCH_INSTANCES:
            raise ValidationError(_("Too many applications in one batch (max {})").format(MAX_BATCH_INSTANCES))
        valid_pks, failures = normalize_uuid_pks(pks)
        succeeded, handled_pks = 0, set()
        queryset = self._with_my_pending_tasks(
            self.filter_queryset(self.get_queryset()).filter(pk__in=valid_pks), request.user
        )
        for instance in queryset:
            handled_pks.add(str(instance.pk))
            task = self._prefetched_my_task(instance)
            if task is None:
                failures.append({"pk": str(instance.pk), "detail": str(_("No pending task for you"))})
                continue
            ok, detail = reject_task(task.pk, request.user, reason)
            if ok:
                succeeded += 1
            else:
                failures.append({"pk": str(instance.pk), "detail": str(detail)})
        # 同 batch_approve：非法 UUID 已在归一阶段记失败，不能重复追加「不可见」
        failures.extend(missing_pk_failures(valid_pks, handled_pks))
        return ApiResponse(
            data={"succeeded": succeeded, "failures": failures},
            detail=_("Operation successful. Rejected {} data").format(succeeded),
        )

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR) or {}),
                    "username": build_basic_type(OpenApiTypes.STR),
                    "comment": build_basic_type(OpenApiTypes.STR),
                },
                required=["pks", "username"],
                description="实例主键列表 + 转交目标用户名 + 说明（逐条独立校验）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-transfer")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def batch_transfer(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """批量转交

        把我的多条待办一次性转给同一用户（逐条独立，返回成功数 + 失败明细）

        与单条同口径：仅「我的当前待办」可转；无待办的实例计入失败明细而非整体拒绝
        （勾选里混入他人任务/已流转实例时，仍完成可转的部分）。
        """
        ensure_approval_action_confirmed(request, "transfer")
        pks = request.data.get("pks") or []
        if not isinstance(pks, (list, tuple)) or not pks:
            return ApiResponse(code=1001, detail=_("Please select the applications to transfer"))
        if len(pks) > MAX_BATCH_INSTANCES:
            return ApiResponse(
                code=1001,
                detail=_("Too many applications in one batch (max {})").format(MAX_BATCH_INSTANCES),
            )
        valid_pks, failures = normalize_uuid_pks(pks)
        username = (request.data.get("username") or "").strip()
        if not username:
            return ApiResponse(code=1001, detail=_("Please select the user to transfer to"))
        comment = (request.data.get("comment") or "").strip()

        # 取值域收敛：只能对自己可见的实例操作（越权 pk 直接计失败，不泄露存在性）
        visible = self._with_my_pending_tasks(
            visible_instances_for(request.user).filter(pk__in=valid_pks), request.user
        )
        instance_map = {str(instance.pk): instance for instance in visible}
        succeeded = 0
        for pk in valid_pks:
            instance = instance_map.get(str(pk))
            if instance is None:
                failures.append({"pk": str(pk), "detail": str(_("No visible application for the given id"))})
                continue
            task = self._prefetched_my_task(instance) if instance.current_node_id else None
            if task is None:
                failures.append({"pk": str(pk), "detail": str(_("No pending task available for the current user"))})
                continue
            ok, detail = transfer_task(task.pk, request.user, username, comment)
            if ok:
                succeeded += 1
            else:
                failures.append({"pk": str(pk), "detail": detail})
        if not succeeded:
            # 全失败：以整体失败返回（前端按普通业务失败提示，明细随 data 带回）
            detail = failures[0]["detail"] if failures else str(_("The approval task has been transferred"))
            return ApiResponse(code=1001, detail=detail, data={"succeeded": 0, "failures": failures})
        return ApiResponse(
            data={"succeeded": succeeded, "failures": failures},
            detail=_("{succeeded} transferred, {failed} failed").format(succeeded=succeeded, failed=len(failures)),
        )
