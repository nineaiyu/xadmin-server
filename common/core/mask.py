#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""输出侧脱敏统一入口：豁免判定 + 规则加载 + 逐字段掩码（唯一实现）。

消费方（口径必须一致，不能再各写一份）：

- ``BaseModelSerializer.to_representation``：列表/详情/导出主链路；
- ``BasePrimaryKeyRelatedField.to_representation``：关联字段 attrs 输出——
  嵌套输出同样受目标模型规则约束（历史实现只掩码主链路，嵌套 attrs 靠"恰好无
  敏感字段"侥幸通过，后续给关联 attrs 加字段即静默绕过）；
- ``system.search``：全局搜索分组输出（同款"声明式字段不掩码"风险面）。

豁免口径与字段权限同源：超管 / 显式 ``ignore_field_permission`` / 原文通道
（``?mask=false`` 且对当前地址有更新权限，放行时按请求留一条审计日志）。
"""

from common.utils import get_logger
from system.services import apply_mask, get_mask_rules, record_original_channel_access

logger = get_logger(__name__)


def mask_exempt(request, user, model=None, ignore_field_permission=False) -> bool:
    """脱敏豁免判定（与 get_allow_fields 同口径）：超管 / 显式豁免 / 原文通道。

    原文通道 = 显式 ``?mask=false`` 且当前用户对该菜单有更新权限。编辑弹窗依赖
    列表行数据，若拿不到原文则会把掩码值回写（to_internal_value 另有兜底守护）；
    仅有更新权限的用户才被放行，只读用户的列表/详情/导出仍按规则掩码。
    放行的原文访问会记一条审计日志（每个请求一次，含模型标识）。
    """
    if user is None or not hasattr(user, "is_superuser"):
        return True
    if user.is_superuser or ignore_field_permission or getattr(request, "ignore_field_permission", False):
        return True
    if request is None:  # 无请求上下文（后台任务/搜索等）不享受原文通道
        return False
    params = getattr(request, "query_params", None) or getattr(request, "GET", None)
    if not params:
        return False
    if str(params.get("mask", "")).lower() not in ("false", "0", "no"):
        return False
    cached = getattr(request, "_mask_original_allowed", None)
    if cached is None:
        # 惰性 import：common 层不引 system 视图层（跨 app 门禁许可函数内惰性 import）
        from common.core.permission import user_can_update_menu

        # 按请求地址判定「对该资源的更新权限」（GET/PUT/PATCH 是三条不同菜单，
        # 按菜单主键比对会让 GET 详情请求永远拿不到放行）
        cached = user_can_update_menu(user, getattr(request, "path_info", None) or getattr(request, "path", "") or "")
        try:
            request._mask_original_allowed = cached
        except AttributeError:  # 只读请求对象兜底
            pass
    if cached:
        # 审计内部按请求去重，列表逐行调用也只记一次
        record_original_channel_access(request, user, model._meta.label_lower if model is not None else None)
    return cached


def mask_role_pks(request, user) -> set:
    """当前用户角色 pk 集合（按请求缓存，避免列表逐行 N+1 查询）。"""
    if user is None or not hasattr(user, "roles"):
        return set()
    cached = getattr(request, "_mask_role_pks", None) if request is not None else None
    if cached is not None:
        return cached
    try:
        role_pks = set(user.roles.values_list("pk", flat=True))
    except Exception:  # noqa: BLE001 非 UserInfo 用户（Anonymous 等）兜底
        role_pks = set()
    if request is not None:
        try:
            request._mask_role_pks = role_pks
        except AttributeError:  # 只读请求对象兜底
            pass
    return role_pks


def apply_related_output_mask(field, data, value):
    """关联字段（``BasePrimaryKeyRelatedField.attrs``）输出掩码的唯一实现。

    请求上下文缺失（celery 内序列化等）时原样返回；豁免口径与主链路一致——
    字段自身 / 根序列化器链上的 ``ignore_field_permission`` / 请求级豁免统一由
    ``mask_exempt`` 判定。字段类只负责懒加载 request 并在拼 label 之前调用本函数。
    """
    request = getattr(field, "request", None)
    user = getattr(request, "user", None) if request is not None else None
    if user is None:
        return data
    ignore = bool(getattr(field, "ignore_field_permission", False))
    node = getattr(field, "parent", None)
    while node is not None and not ignore:
        ignore = bool(getattr(node, "ignore_field_permission", False))
        node = getattr(node, "parent", None)
    return apply_output_mask(data, request, user, value._meta.model, ignore)


def apply_output_mask(data, request, user, model, ignore_field_permission=False):
    """按 model 的脱敏规则对输出字典逐字段掩码；豁免 / 无规则 / 非字典时原样返回。

    规则按 sort 升序加载；同字段「sort 小者优先」——首个命中（字段匹配 + 角色匹配）
    的规则生效，其后该字段规则跳过；不同字段互不干扰。就地修改并返回入参字典。
    """
    if not isinstance(data, dict) or not data or model is None or user is None:
        return data
    if mask_exempt(request, user, model, ignore_field_permission=ignore_field_permission):
        return data
    rules = get_mask_rules(model._meta.label_lower)
    if not rules:
        return data
    role_pks = mask_role_pks(request, user)
    masked_fields = set()
    for rule in rules:
        field_name = rule["field"]
        if field_name in masked_fields or field_name not in data:
            continue
        if rule["roles"] and not bool(role_pks & set(rule["roles"])):
            continue
        data[field_name] = apply_mask(data[field_name], rule)
        masked_fields.add(field_name)
    return data
