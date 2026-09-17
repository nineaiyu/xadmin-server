# -*- coding: utf-8 -*-
"""ViewSet 覆写守护：批量删除路径与 perform_destroy 覆写的契约。

框架批量删除有两条路径（common/core/modelset/batch.py）：
- 非逐行分支：filter 后直接 queryset.delete()，**不会调用 perform_destroy**；
- 逐行分支（软删模型 / 带文件清理的模型）：逐行 self.perform_destroy(instance)。

视图覆写 perform_destroy 承担副作用（踢线、派生数据清理、保护性校验）时，走非逐行
分支会把这些副作用静默跳过——历史同类缺陷：知识库批量删除残留孤儿分块；在线用户
批量删除不踢线（本守护落地时修复）。

契约：暴露 batch-destroy 路由且覆写 perform_destroy 的 ViewSet，必须满足其一：
1. 视图自行覆写 batch_destroy（批量语义由视图自担）；
2. 模型满足逐行条件（_needs_rowwise_delete() 为 True）；
3. 登记在下方豁免表（必须写明理由）。
"""

from rest_framework.mixins import DestroyModelMixin

from common.core.modelset.base import BaseViewSet
from common.core.modelset.batch import BatchDestroyAction
from system.utils.permission_sync.scan import build_route_index

# 豁免登记：{ViewSet 类名: 理由}。新增条目必须说明批量路径为何可以跳过 perform_destroy。
EXEMPT_VIEWSETS = {}

# 框架默认 perform_destroy（无自定义副作用）
DEFAULT_PERFORM_DESTROY_OWNERS = (BaseViewSet, DestroyModelMixin)


def _defining_class(cls, attr):
    """属性最终归属类（MRO 中首个定义者）；未定义返回 None。"""
    for klass in cls.__mro__:
        if attr in klass.__dict__:
            return klass
    return None


def _batch_destroy_viewsets():
    """暴露 batch-destroy 路由的 ViewSet（按类去重）。"""
    viewsets = {}
    for route in build_route_index():
        if route.view_cls is None or "batch_destroy" not in (route.actions or {}).values():
            continue
        viewsets[route.view_cls.__name__] = route.view_cls
    return viewsets


def test_overridden_perform_destroy_is_reachable_by_batch_delete():
    problems = []
    for name, cls in sorted(_batch_destroy_viewsets().items()):
        owner = _defining_class(cls, "perform_destroy")
        if owner is None or owner in DEFAULT_PERFORM_DESTROY_OWNERS:
            continue  # 未覆写：批量路径无自定义副作用可跳过
        if name in EXEMPT_VIEWSETS:
            continue
        if _defining_class(cls, "batch_destroy") is not BatchDestroyAction:
            continue  # 自带批量覆写：批量语义由视图自担
        try:
            rowwise = cls()._needs_rowwise_delete()
        except Exception as exc:  # noqa: BLE001 判定失败按不通过处理，守护不允许静默放行
            problems.append(f"{name}: 无法判定逐行条件（{exc}）")
            continue
        if not rowwise:
            problems.append(
                f"{name}: 覆写了 perform_destroy，但模型批量路径为非逐行——批量删除会跳过该覆写；"
                f"请覆写 batch_destroy（记得带 @action 装饰器）或让 _needs_rowwise_delete 返回 True"
            )
    assert not problems, "ViewSet 覆写契约被破坏：\n" + "\n".join(problems)
