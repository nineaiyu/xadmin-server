#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""软删除模型的回收站 Action（recycle 列表 / restore 恢复 / purge 物理清除）。

仅对混入本类且模型继承 SoftDeleteModel 的视图集生效：
- recycle:  查看 all_objects 中已软删除的数据（数据权限过滤照常生效）；
- restore:  按 pks 恢复（清空 deleted_at）；恢复必须逐行 save()（而非 queryset.update），
            post_save 信号照常触发，权限缓存失效链路才不会漏；
- purge:    按 pks 物理清除（走 hard_delete，文件清理/级联照常触发）；
            不传 pks 时清除超过 RECYCLE_BIN_RETENTION_DAYS 的全部数据。

菜单等"成组恢复/清除"语义的模型，覆写 get_recycle_restore_queryset /
get_recycle_purge_queryset 两个 hook 即可，不要整段复制 action。
"""

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.decorators import action

from common.contracts import ensure_impact_confirmed
from common.core.modelset.batch import normalize_pks
from common.core.response import ApiResponse
from common.settings_contract import kernel_setting
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger

logger = get_logger(__name__)

# drf-spectacular 中字符串类型为 STR（无 STRING 别名）
_PKS_ARRAY = build_array_type(build_basic_type(OpenApiTypes.STR) or {})


def _parse_pks(request: Any, model: Any, *, required: bool, empty_detail: Any = None) -> Any:
    """回收站 ``pks`` 入参解析：显式提供时做模型级主键归一，非法即拒绝。

    返回 ``(pks, error)`` 三元语义：

    - ``error`` 非 None：参数非法，直接返回给客户端——**绝不能**把「归一失败」
      折叠成空列表，否则 purge 会落入「清除全部超期数据」分支（越权清库）；
    - ``pks`` 非 None：按选中主键处理；
    - 两者皆 None：未提供 ``pks``（仅 purge 的保留期清扫语义）。

    JSON 数组等非对象请求体、非数组 / 空数组 / 非法主键（如向 UUID 主键传
    任意字符串）一律显式报错，不再静默按空处理。
    """
    empty_detail = empty_detail or _("Please select the data")
    data = request.data
    if not isinstance(data, dict):
        return None, ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
    if "pks" not in data:
        if required:
            return None, ApiResponse(code=1001, detail=empty_detail)
        return None, None
    raw = data.get("pks")
    if not isinstance(raw, (list, tuple)):
        return None, ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
    if not raw:
        return None, ApiResponse(code=1001, detail=empty_detail)
    valid, invalid = normalize_pks(model, raw)
    # 空值不算非法主键但也不能作为选中项：整批只有空值时等同于未选
    valid = [pk for pk in valid if pk not in (None, "")]
    if invalid or not valid:
        return None, ApiResponse(code=1004, detail=_("Operation failed. Abnormal data"))
    return valid, None


class RecycleBinAction:
    if TYPE_CHECKING:  # 宿主 ViewSet 提供的接口（mixin 模式）

        def get_queryset(self) -> Any: ...

        def filter_queryset(self, queryset: Any) -> Any: ...

        def list(self, request: Any, *args: Any, **kwargs: Any) -> Any: ...

    def get_recycle_restore_queryset(self, pks: Any) -> Any:
        """恢复目标查询集（已含数据权限过滤）；成组语义的模型可覆写扩展范围。"""
        model = self.get_queryset().model
        queryset = model.all_objects.filter(deleted_at__isnull=False, pk__in=pks)
        return self.filter_queryset(queryset)

    def get_recycle_purge_queryset(self, pks: Any) -> Any:
        """物理清除目标（已含数据权限过滤）：选中 pks，或不传 pks 时清除超过保留期的数据。"""
        model = self.get_queryset().model
        queryset = model.all_objects.filter(deleted_at__isnull=False)
        if pks:
            queryset = queryset.filter(pk__in=pks)
        else:
            retention_days = kernel_setting("RECYCLE_BIN_RETENTION_DAYS")
            cutoff = timezone.now() - timedelta(days=retention_days)
            queryset = queryset.filter(deleted_at__lt=cutoff)
        return self.filter_queryset(queryset)

    @extend_schema(
        request=OpenApiRequest(build_object_type(properties={"pks": _PKS_ARRAY})),
        responses=get_default_response_schema(),
    )
    @action(methods=["patch"], detail=False, url_path="recycle/restore")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def recycle_restore(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """从回收站恢复{cls}数据"""
        model = self.get_queryset().model
        pks, error = _parse_pks(request, model, required=True, empty_detail=_("Please select the data to restore"))
        if error is not None:
            return error
        count = 0
        skipped = 0
        for instance in self.get_recycle_restore_queryset(pks):
            instance.deleted_at = None
            try:
                # ATOMIC_REQUESTS 开启时这里是 savepoint，单行冲突不毒化整个请求事务
                with transaction.atomic():
                    instance.save(update_fields=["deleted_at"])
                count += 1
            except IntegrityError:
                # 活跃数据已占用唯一键（如角色 code、菜单 name）时跳过该行，
                # 其余行照常恢复；返回可读计数而非 500
                skipped += 1
                logger.warning(f"restore skipped due to unique conflict: {instance}")
        detail = _("Restored {} data").format(count)
        if skipped:
            detail += ", " + _("Skipped {} data with unique conflicts").format(skipped)
        return ApiResponse(detail=detail)

    @extend_schema(
        request=OpenApiRequest(build_object_type(properties={"pks": _PKS_ARRAY})),
        responses=get_default_response_schema(),
    )
    @action(methods=["delete"], detail=False, url_path="recycle/purge")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def recycle_purge(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """物理清除{cls}回收站数据

        不传 pks 时清除全部超过保留期的数据
        """
        model = self.get_queryset().model
        pks, error = _parse_pks(request, model, required=False)
        if error is not None:
            return error
        queryset = self.get_recycle_purge_queryset(pks)
        # 复用删除影响面守卫：登记模型仍有引用时要求显式确认（与 destroy/batch 同口径），
        # 否则「彻底删除」可绕过引用保护直接清库
        ensure_impact_confirmed(self, request, queryset=queryset)
        count = 0
        skipped = 0
        # 经 filter_queryset 后不保证是 QuerySet（过滤器/取值域可返回 list），
        # 因此保留 iterator 能力判断：是 QuerySet 时走游标分批，否则按可迭代对象处理
        for instance in queryset.iterator() if hasattr(queryset, "iterator") else queryset:
            try:
                # 逐行 savepoint：单行硬删失败（PROTECT 引用等）不毒化整批
                with transaction.atomic():
                    instance.hard_delete()  # 走原始 delete 链，物理文件/级联照常清理
                count += 1
            except IntegrityError:
                skipped += 1
                logger.warning(f"purge skipped due to integrity constraint: {instance}")
        detail = _("Purged {} data").format(count)
        if skipped:
            detail += ", " + _("Skipped {} data with integrity constraints").format(skipped)
        return ApiResponse(detail=detail)

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["get"], detail=False, url_path="recycle")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def recycle(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取{cls}回收站列表"""
        model = self.get_queryset().model
        self.queryset = model.all_objects.filter(deleted_at__isnull=False).order_by("-deleted_at")
        # 借用 list action 的口径：list_serializer_class 选择（回收站列需含 deleted_at）
        # 与 auto_prefetch 的 N+1 优化（recycle 同为逐行序列化的列表路径）。
        # self.action 是请求级实例属性（DRF 每请求新建 ViewSet 实例），改写不会跨请求泄漏；
        # 且经 finally 复位，异常路径也不影响同一实例内的后续调用。
        self.action = "list"
        try:
            return self.list(request, *args, **kwargs)
        finally:
            self.action = "recycle"
