#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""批量操作 Action：排序（rank）、批量删除（batch-destroy）与批量更新（batch-update）。

拆分自 modelset.py。
"""

from collections.abc import Callable

from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger
from system.services import ensure_impact_confirmed

logger = get_logger(__name__)

# 排序入参上限：Case/When 的 WHEN 数随列表线性增长，超大列表会撑爆 SQL 参数/表达式上限
RANK_MAX_ITEMS = 1000
# 批量更新入参上限：逐项走序列化器校验（写放大），与导入口径一致的量级约束
BATCH_MAX_ITEMS = 500


class RankAction:
    filter_queryset: Callable
    get_queryset: Callable

    @extend_schema(
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR))),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="rank")
    def rank(self, request, *args, **kwargs):
        """{cls}排序"""
        # 入参必须是主键列表：dict 会被 list() 解包成键列表，非法形态直接返回可读错误
        if not isinstance(request.data, (list, tuple)):
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        pks = [pk for pk in request.data if pk not in (None, "")]
        if len(pks) > RANK_MAX_ITEMS:
            return ApiResponse(code=1004, detail=_("Too many items to sort (max {})").format(RANK_MAX_ITEMS))
        if pks:
            # Case/When 单条批量 UPDATE，替代逐条 filter(pk=pk).update(rank=rank)
            queryset = self.filter_queryset(self.get_queryset()).filter(pk__in=pks)
            queryset.update(
                rank=Case(
                    *[When(pk=pk, then=Value(index)) for index, pk in enumerate(pks, start=1)],
                    output_field=IntegerField(),
                )
            )
        return ApiResponse(detail=_("Sorting saved successfully"))


class BatchDestroyAction:
    filter_queryset: Callable
    get_queryset: Callable
    perform_destroy: Callable

    @extend_schema(
        request=OpenApiRequest(build_array_type(build_basic_type(OpenApiTypes.STR))),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-destroy")
    def batch_destroy(self, request, *args, **kwargs):
        """批量删除{cls}"""

        # 入参必须是主键列表（与 rank 同口径）：防 dict 等非法形态按键误删，
        # 且候选池核查结论——pk__in=[] 为空集，天然不存在无 id 条件的全表删除
        if not isinstance(request.data, (list, tuple)):
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        queryset = self.get_queryset()
        # 主键类型安全规范化：非法形态（如给自增整数主键传非数字串）归入失败明细，
        # 不能整批 500
        valid_pks, failures = _normalize_pks(queryset.model, request.data)
        queryset = self.filter_queryset(queryset).filter(pk__in=valid_pks)
        # 引用保护：登记在 IMPACT_GUARD_MODELS 的模型有影响面时要求显式确认。
        # 必须在分支前统一校验——逐行分支的 perform_destroy 异常会被吞（只记日志），
        # 放在分支内会造成「静默不删但提示成功」。
        ensure_impact_confirmed(self, request, queryset=queryset)
        # 批量响应补逐项明细（data.success / data.failures）。
        # detail 文案与历史口径一致（既有前端与测试只读 detail），明细为增量字段。
        existing = [str(pk) for pk in queryset.values_list("pk", flat=True)]
        if not self._needs_rowwise_delete():
            # 模型无逐行副作用时直接走批量 delete()，单条 SQL 完成
            # （旧实现逐行 instance.delete()，N 行 = N 次级联删除事务）
            deleted, _rows_count = queryset.delete()
            return ApiResponse(
                detail=_("Operation successful. Batch deleted {} data").format(deleted),
                data={"success": existing, "failures": failures},
            )

        # 软删模型需要触发 delete() 中的 save() 信号（权限缓存失效依赖此链路）；
        # 带文件字段的模型需要触发模型 delete() 以清理底层文件。先收集再统一删除
        count = 0
        success = []
        for instance in queryset:
            try:
                result = self.perform_destroy(instance)
                # Django delete() 返回 (total, per_model_dict) 元组；
                # 软删模型的 delete() 返回标记行数（int）
                deleted = result[0] if isinstance(result, tuple) else (result or 0)
                if deleted:
                    count += 1
                    success.append(str(instance.pk))
                else:
                    failures.append({"pk": str(instance.pk), "reason": str(_("Not deleted"))})
            except Exception as e:
                logger.error(f"failed to destroy instance {instance} with error {e}")
                failures.append({"pk": str(instance.pk), "reason": _batch_error_message(e)})
        return ApiResponse(
            detail=_("Operation successful. Batch deleted {} data").format(count),
            data={"success": success, "failures": failures},
        )

    def _needs_rowwise_delete(self):
        """是否需要逐行 delete() 以触发模型级副作用。

        - 继承 AutoCleanFileMixin 且确实存在文件/附件关联的模型，delete()
          才有批量 delete() 覆盖不到的文件清理副作用；
        - 软删模型（SoftDeleteModel）的 delete() 走 save()，post_save 信号
          （权限缓存失效）必须触发，单 SQL update 会绕过信号。

        视图层另有逐行副作用（如踢线下线）时，视图可覆写本方法返回 True——
        这类副作用只在 perform_destroy 中，非逐行分支会静默跳过（范例
        UserOnlineViewSet；覆写契约见 docs/architecture/framework-cookbook.md）。
        """
        model = getattr(getattr(self, "queryset", None), "model", None)
        if model is None:
            return True
        from common.core.models import AutoCleanFileMixin, SoftDeleteModel

        if issubclass(model, SoftDeleteModel):
            return True
        return issubclass(model, AutoCleanFileMixin) and AutoCleanFileMixin.has_file_cleanup(model)


def _normalize_pks(model, pks):
    """批量入参 pk 的类型安全规范化，返回 (合法值, 非法项明细)。

    主键形态因模型而异（自增整数 / UUID / 字符串），直接把原始入参交给
    ``filter(pk__in=...)`` 会因类型不匹配抛 ValueError（整批 500）；
    非法项应作为逐项失败明细返回，而不是让整批请求失败。
    """
    pk_field = model._meta.pk
    valid, invalid = [], []
    for pk in pks:
        try:
            valid.append(pk_field.to_python(pk))
        except Exception:  # noqa: BLE001 非法主键值统一归入失败明细
            invalid.append({"pk": str(pk), "reason": str(_("Not found or no permission"))})
    return valid, invalid


def _batch_error_message(exc, limit=200):
    """批量逐项失败原因的可读归一（DRF ValidationError → 字段: 消息）。"""
    detail = getattr(exc, "detail", None)
    if detail:
        if isinstance(detail, dict):
            parts = []
            for field, msgs in detail.items():
                msg = msgs[0] if isinstance(msgs, (list, tuple)) and msgs else msgs
                parts.append(f"{field}: {msg}")
            return "; ".join(parts)[:limit]
        if isinstance(detail, (list, tuple)) and detail:
            return str(detail[0])[:limit]
        return str(detail)[:limit]
    return str(exc)[:limit]


class BatchPartialUpdateAction:
    """通用批量更新：逐项走序列化器校验，按项隔离事务（部分成功语义）。

    视图声明 ``batch_update_fields`` 字段白名单（list/tuple/dict 均可，服务端只取字段名）：

    - 白名单之外的字段整批拒绝（fail-closed）——批量入口不能成为绕过字段权限的后门；
    - 未声明字段/白名单的视图不提供批量更新能力（返回可读错误，等价于未开放）；
    - 逐项 ``get_serializer(instance, data=fields, partial=True)``，与单条更新同口径：
      字段级权限裁剪、业务校验、save() 信号全部生效；
    - 单项失败不影响其余项，统一返回 ``data={"success": [...], "failures": [{"pk", "reason"}]}``。
    """

    filter_queryset: Callable
    get_queryset: Callable
    get_serializer: Callable
    perform_update: Callable

    # 视图声明：允许批量修改的字段白名单（None/空 = 未开放批量更新）
    batch_update_fields = ()

    def get_batch_update_fields(self) -> set:
        fields = self.batch_update_fields or ()
        if isinstance(fields, dict):
            fields = fields.keys()
        return {str(field) for field in fields}

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "pks": build_array_type(build_basic_type(OpenApiTypes.STR)),
                    "fields": build_object_type(),
                },
                required=["pks", "fields"],
                description="批量更新：主键列表 + 待修改字段（白名单校验）",
            )
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=["post"], detail=False, url_path="batch-update")
    def batch_update(self, request, *args, **kwargs):
        """批量修改{cls}"""
        allowed = self.get_batch_update_fields()
        if not allowed:
            return ApiResponse(code=1004, detail=_("Batch update is not supported for this resource"))

        payload = request.data
        if not isinstance(payload, dict):
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        pks = payload.get("pks") or []
        fields = payload.get("fields") or {}
        if not isinstance(pks, (list, tuple)) or not isinstance(fields, dict) or not fields:
            return ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
        if len(pks) > BATCH_MAX_ITEMS:
            return ApiResponse(code=1004, detail=_("Too many items to update (max {})").format(BATCH_MAX_ITEMS))
        invalid = sorted(set(fields) - allowed)
        if invalid:
            return ApiResponse(
                code=1004,
                detail=_("Fields not allowed for batch update: {}").format(", ".join(invalid)),
            )

        queryset = self.get_queryset()
        valid_pks, failures = _normalize_pks(queryset.model, pks)
        queryset = self.filter_queryset(queryset).filter(pk__in=valid_pks)
        instances = {str(obj.pk): obj for obj in queryset}
        success = []
        for pk in valid_pks:
            instance = instances.get(str(pk))
            if instance is None:
                failures.append({"pk": str(pk), "reason": str(_("Not found or no permission"))})
                continue
            try:
                with transaction.atomic():
                    serializer = self.get_serializer(instance, data=fields, partial=True)
                    serializer.is_valid(raise_exception=True)
                    self.perform_update(serializer)
                success.append(str(pk))
            except Exception as e:  # noqa: BLE001 单项失败不影响其余项（部分成功语义）
                logger.warning("batch update item failed. pk:%s error:%s", pk, e)
                failures.append({"pk": str(pk), "reason": _batch_error_message(e)})
        return ApiResponse(
            data={"success": success, "failures": failures, "updated": len(success)},
            detail=_("Batch update completed: {} succeeded, {} failed").format(len(success), len(failures)),
        )
