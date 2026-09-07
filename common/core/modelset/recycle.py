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

from django.conf import settings
from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiRequest
from rest_framework.decorators import action

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from common.utils import get_logger

logger = get_logger(__name__)

# drf-spectacular 中字符串类型为 STR（无 STRING 别名）
_PKS_ARRAY = build_array_type(build_basic_type(OpenApiTypes.STR))


class RecycleBinAction(object):
    def get_recycle_restore_queryset(self, pks):
        """恢复目标查询集（已含数据权限过滤）；成组语义的模型可覆写扩展范围。"""
        model = self.get_queryset().model
        queryset = model.all_objects.filter(deleted_at__isnull=False, pk__in=pks)
        return self.filter_queryset(queryset)

    def get_recycle_purge_queryset(self, pks):
        """物理清除目标（已含数据权限过滤）：选中 pks，或不传 pks 时清除超过保留期的数据。"""
        model = self.get_queryset().model
        queryset = model.all_objects.filter(deleted_at__isnull=False)
        if pks:
            queryset = queryset.filter(pk__in=pks)
        else:
            retention_days = getattr(settings, 'RECYCLE_BIN_RETENTION_DAYS', 30)
            cutoff = timezone.now() - timedelta(days=retention_days)
            queryset = queryset.filter(deleted_at__lt=cutoff)
        return self.filter_queryset(queryset)

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(properties={'pks': _PKS_ARRAY})
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=['patch'], detail=False, url_path='recycle/restore')
    def recycle_restore(self, request, *args, **kwargs):
        """从回收站恢复{cls}数据"""
        pks = request.data.get('pks') or []
        if not pks:
            return ApiResponse(code=1001, detail=_("Please select the data to restore"))
        count = 0
        skipped = 0
        for instance in self.get_recycle_restore_queryset(pks):
            instance.deleted_at = None
            try:
                # ATOMIC_REQUESTS 开启时这里是 savepoint，单行冲突不毒化整个请求事务
                with transaction.atomic():
                    instance.save(update_fields=['deleted_at'])
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
        request=OpenApiRequest(
            build_object_type(properties={'pks': _PKS_ARRAY})
        ),
        responses=get_default_response_schema(),
    )
    @action(methods=['delete'], detail=False, url_path='recycle/purge')
    def recycle_purge(self, request, *args, **kwargs):
        """物理清除{cls}回收站数据（不传 pks 时清除全部超过保留期的数据）"""
        queryset = self.get_recycle_purge_queryset(request.data.get('pks') or [])
        count = 0
        for instance in queryset.iterator() if hasattr(queryset, "iterator") else queryset:
            instance.hard_delete()  # 走原始 delete 链，物理文件/级联照常清理
            count += 1
        return ApiResponse(detail=_("Purged {} data").format(count))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=['get'], detail=False, url_path='recycle')
    def recycle(self, request, *args, **kwargs):
        """获取{cls}回收站列表"""
        model = self.get_queryset().model
        self.queryset = model.all_objects.filter(deleted_at__isnull=False).order_by('-deleted_at')
        # 借用 list action 的口径：list_serializer_class 选择（回收站列需含 deleted_at）
        # 与 auto_prefetch 的 N+1 优化（recycle 同为逐行序列化的列表路径）
        self.action = 'list'
        try:
            return self.list(request, *args, **kwargs)
        finally:
            self.action = 'recycle'
