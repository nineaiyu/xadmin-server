#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""AI 动作参数行级复核（自 ai_actions 拆分，行为不变）。

现有服务端校验覆盖字段与格式（菜单权限点 + 序列化器），但不校验「参数指向的 pk
是否在调用者数据权限内」——参数里的 pk 由 LLM 产出，可能指向权限外对象。本模块
对声明式 API 动作（可解析 ViewSet 与模型）且参数含单一标量主键时，按调用者数据
权限再查一次；不可达即拒绝。解析失败/无模型声明/只读动作一律跳过（不阻断，
避免误杀既有能力）。
"""

from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)


def verify_action_target(user, spec, params) -> str:
    """动作参数行级复核：参数指向的目标对象必须在调用者数据权限内可达。

    返回不可达原因（可读文案），空串 = 通过。
    """
    from django.urls import Resolver404, resolve

    from ai.utils.ai_api_actions import ApiActionSpec, build_action_url, resolve_api_params
    from common.core.filter import get_filter_queryset

    if not isinstance(spec, ApiActionSpec) or str(spec.method).upper() == "GET":
        return ""
    path_params, __body, __query, error = resolve_api_params(spec, user, params if isinstance(params, dict) else {})
    if error:
        return ""
    url = build_action_url(spec, path_params)
    if url is None:
        return ""
    pk = None
    for name, value in (path_params or {}).items():
        if name in ("pk", "id") or name.endswith(("_pk", "_id")):
            pk = value
            break
    if pk in (None, ""):
        return ""
    try:
        match = resolve(url.split("?", 1)[0])
    except Resolver404:
        return ""
    view_class = getattr(match.func, "cls", None)
    model = getattr(getattr(view_class, "queryset", None), "model", None)
    if model is None:
        return ""
    try:
        reachable = get_filter_queryset(model.objects.all(), user).filter(pk=pk).exists()
    except Exception:  # noqa: BLE001 主键形态不符/模型查询异常：跳过复核（不误杀）
        logger.info("ai action target check skipped. action:%s pk:%s", getattr(spec, "key", ""), pk, exc_info=True)
        return ""
    if not reachable:
        return str(_("The target object does not exist or you do not have permission to access it"))
    return ""
