#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""CRUD 五个基础 Action：Create / Detail / List / Destroy / Update。

统一将 DRF 原生响应包装为 ApiResponse。拆分自 modelset.py。
"""

from rest_framework import mixins

from common.core.response import ApiResponse
from common.utils import get_logger
from server.utils import get_current_request

logger = get_logger(__name__)


class CreateAction(mixins.CreateModelMixin):
    def create(self, request, *args, **kwargs):
        """添加{cls}数据"""
        data = super().create(request, *args, **kwargs).data
        return ApiResponse(data=data)


class DetailAction(mixins.RetrieveModelMixin):
    def retrieve(self, request, *args, **kwargs):
        """获取{cls}的详情"""
        data = super().retrieve(request, *args, **kwargs).data
        return ApiResponse(data=data)


class ListAction(mixins.ListModelMixin):
    def list(self, request, *args, **kwargs):
        """获取{cls}的列表"""
        data = super().list(request, *args, **kwargs).data
        if isinstance(data, dict) and request.query_params.get("with_meta", "").lower() in ("1", "true", "yes"):
            # 按 with_meta=1 内联元数据，页面首开把
            # list / search-columns / search-fields 三个请求合并为一个
            self.inline_metadata(request, data)
        return ApiResponse(data=data)

    def inline_metadata(self, request, data: dict) -> None:
        """将 search-columns / search-fields 载荷内联进列表响应。

        仅在视图集混入了对应元数据 Action 时生效；单条元数据构建失败
        只记录日志并降级省略，绝不影响列表本身。
        """
        for action_name, key in (
            ("search_columns", "search_columns"),
            ("search_fields", "search_fields"),
        ):
            action = getattr(self, action_name, None)
            if action is None:
                continue
            try:
                result = action(request)
                payload = result.data.get("data")
                if payload is not None:
                    data[key] = payload
            except Exception as e:
                logger.warning(f"inline metadata {action_name} failed on {self.__class__.__name__}: {e}")


class DestroyAction(mixins.DestroyModelMixin):
    def destroy(self, request, *args, **kwargs):
        """删除{cls}数据"""
        instance = self.get_object()
        self.perform_destroy(instance)
        return ApiResponse()


class UpdateAction(mixins.UpdateModelMixin):
    # diff 中忽略的审计/时间字段
    AUDIT_DIFF_IGNORED_FIELDS = {"created_time", "updated_time", "date_changed", "pk", "id"}

    def update(self, request, *args, **kwargs):
        """整体更新{cls}信息"""
        old_values = self._audit_diff_old_values(kwargs.get("pk"))
        data = super().update(request, *args, **kwargs).data
        if old_values is not None:
            self._stash_audit_changes(old_values)
        return ApiResponse(data=data)

    def _audit_diff_old_values(self, pk):
        """AUDIT_DIFF_MODELS 白名单模型的 update 路径，取更新前快照用于计算 diff。

        白名单为空（默认）时零开销直接返回；命中白名单的更新额外产生 2 次查询
        （更新前快照 + 更新后回读），按需开启。白名单走 SysConfig.AUDIT_DIFF_MODELS
        （系统配置优先，未登记回退 settings），管理员可运行时扩容。
        """
        from common.core.config import SysConfig

        whitelist = SysConfig.AUDIT_DIFF_MODELS or []
        model = getattr(getattr(self, "queryset", None), "model", None)
        if not whitelist or not pk or model is None or model._meta.label not in whitelist:
            return None
        return self.get_queryset().filter(pk=pk).values().first()

    def _stash_audit_changes(self, old_values):
        """对比更新前后字段值，把 diff 挂到当前请求上，由 ApiLoggingMiddleware 写入操作日志。"""
        pk = old_values.get("pk") or old_values.get("id")
        new_values = self.get_queryset().filter(pk=pk).values().first()
        if not new_values:
            return
        changes = {}
        for field, old in old_values.items():
            if field in self.AUDIT_DIFF_IGNORED_FIELDS or field not in new_values:
                continue
            new = new_values[field]
            if old != new:
                changes[field] = {
                    "old": str(old) if old is not None else None,
                    "new": str(new) if new is not None else None,
                }
        if changes:
            current_request = get_current_request()
            if current_request is not None:
                # threadlocal 中可能是 DRF Request 包装（IsAuthenticated 会覆盖写入），
                # 统一落到原始 Django request 上，ApiLoggingMiddleware 才能读到；
                # DRF Request.__getattr__ 代理 _request，读侧不受影响
                target = getattr(current_request, "_request", current_request)
                setattr(target, "operation_log_changes", changes)

    def partial_update(self, request, *args, **kwargs):
        """部分更新{cls}信息"""
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)
