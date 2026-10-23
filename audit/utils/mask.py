#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""字段级数据脱敏工具：规则加载（300s 缓存，信号失效）+ apply_mask 纯函数。

- apply_mask 为纯函数，单测主战场；
- get_mask_rules(model_label) 返回按 sort 升序的活动规则（roles 展开为 pk 列表），
  缓存由 DataMaskRule 的 post_save/post_delete/m2m_changed 信号失效（signal_handler.py）。
"""

import json
import re
from typing import Any

from django.apps import apps
from django.core.cache import cache

from common.core.response import API_SUCCESS_CODE
from common.utils import get_logger

logger = get_logger(__name__)

MASK_CACHE_PREFIX = "data_mask_"
MASK_CACHE_TIMEOUT = 300
#: 原文通道审计的 OperationLog 模块名（可按 module 检索）
ORIGINAL_CHANNEL_MODULE = "mask:original"


def _segment(value: str, keep_head: int, keep_tail: int, mask_char: str) -> str:
    """通用分段掩码：保留前 keep_head 与后 keep_tail，中间以 mask_char 填充。
    长度不足以构成掩码区间时原样返回（避免退化成一整串星号破坏可辨识度）。
    """
    n = len(value)
    if n <= (keep_head + keep_tail) or n <= 1:
        return value
    masked_len = n - keep_head - keep_tail
    masked = mask_char * masked_len
    if keep_head:
        masked = value[:keep_head] + masked
    if keep_tail:
        masked = masked + value[n - keep_tail :]
    return masked


def _mask_email(value: str, keep_head: int, mask_char: str) -> str:
    local, sep, domain = value.rpartition("@")
    if not sep:
        return _segment(value, keep_head, 0, mask_char)
    return f"{_segment(local, keep_head, 0, mask_char)}{sep}{domain}"


def custom_pattern_error(pattern: Any) -> re.error | None:
    """试编译自定义正则：非法返回 re.error 实例，合法（或空/非字符串）返回 None。

    保存期校验（序列化器）与预览接口的非法正则标记共用同一判定，
    避免两处对"什么算非法正则"的口径漂移。
    """
    if not pattern or not isinstance(pattern, str):
        return None
    try:
        re.compile(pattern)
    except re.error as exc:
        return exc
    return None


def apply_mask(value: Any, rule: dict[str, Any]) -> Any:
    """按规则对单个值脱敏。空值/非字符串/规则缺省时原样返回。

    dict / list 递归处理（JSON 字段内部同样生效）：规则挂在 JSON 列上时，逐个字符串
    元素应用同一规则——此前非字符串值原样返回，规则对 JSON 字段是静默空操作。

    :param rule: get_mask_rules 产出的规则 dict（mask_type/keep_head/keep_tail/mask_char/pattern）
    """
    if isinstance(value, dict):
        return {key: apply_mask(item, rule) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [apply_mask(item, rule) for item in value]
    if value is None or not isinstance(value, str) or not value:
        return value
    mask_type = rule.get("mask_type")
    keep_head = max(int(rule.get("keep_head") or 0), 0)
    keep_tail = max(int(rule.get("keep_tail") or 0), 0)
    mask_char = rule.get("mask_char") or "*"
    if mask_type == "email":
        return _mask_email(value, keep_head, mask_char)
    if mask_type == "custom":
        pattern = rule.get("pattern")
        if not pattern:
            return value
        try:
            return re.sub(pattern, lambda m: _segment(m.group(0), keep_head, keep_tail, mask_char), value)
        except re.error:
            # 存量脏数据兜底：仅针对历史遗留的非法正则回退原值，保证脱敏管道不因单条坏配置中断；
            # 新配置的非法正则已在保存期被序列化器拒绝，正常链路不会再走到这里。
            return value
    # phone / idcard / name / bankcard 及默认路径统一走分段掩码
    return _segment(value, keep_head, keep_tail, mask_char)


def _first_applicable_rule(field: str, rules: list[dict[str, Any]], role_pks: set[Any]) -> Any:
    """同字段「sort 小者优先」+ 角色交集判定（与 apply_output_mask 同口径）。"""
    for rule in rules:
        if rule.get("field") != field:
            continue
        if rule.get("roles") and not bool(role_pks & set(rule["roles"])):
            continue
        return rule
    return None


def mask_operation_changes(value: Any, model_label: Any, request: Any, user: Any) -> Any:
    """操作日志 ``changes``（``{field: {old, new}}`` JSON）按源模型规则脱敏。

    ``changes`` 是整段 JSON 文本，字段级规则直接套在文本上是空操作（或把整段
    文本当成一个手机号掩码）；这里先解析，再按「源模型 + 变更字段」取规则，对
    old/new 分别掩码，最后重新序列化。查看者角色与原文通道口径与列表脱敏一致：
    超管 / ``?mask=false`` + 更新权限 / 无规则时原样返回。

    历史行没有 ``model_label``（本字段上线前落库）或 JSON 解析失败时原样返回，
    不阻断日志读取。
    """
    from common.core.mask import mask_exempt, mask_role_pks

    if not value or not model_label:
        return value
    if mask_exempt(request, user):
        return value
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return value
    if not isinstance(data, dict):
        return value
    rules = get_mask_rules(str(model_label))
    if not rules:
        return value
    role_pks = mask_role_pks(request, user)
    masked: dict[str, Any] = {}
    for field, diff in data.items():
        if not isinstance(diff, dict):
            masked[field] = diff
            continue
        rule = _first_applicable_rule(field, rules, role_pks)
        if rule is None:
            masked[field] = diff
            continue
        masked[field] = {
            "old": apply_mask(diff.get("old"), rule),
            "new": apply_mask(diff.get("new"), rule),
        }
    return json.dumps(masked, ensure_ascii=False, default=str)


def get_mask_rules(model_label: str) -> list[dict[str, Any]]:
    """取某模型的活动脱敏规则（按 sort 升序），5 分钟缓存。"""
    data_mask_model = apps.get_model("audit", "DataMaskRule")

    def _load() -> list[dict[str, Any]]:
        queryset = data_mask_model.objects.filter(model=model_label, is_active=True).order_by("sort", "created_time")
        return [
            {
                "field": rule.field,
                "mask_type": rule.mask_type,
                "keep_head": rule.keep_head,
                "keep_tail": rule.keep_tail,
                "mask_char": rule.mask_char,
                "pattern": rule.pattern,
                "roles": [pk for pk in rule.roles.values_list("pk", flat=True)],
            }
            for rule in queryset
        ]

    rules: list[dict[str, Any]] = cache.get_or_set(f"{MASK_CACHE_PREFIX}{model_label}", _load, MASK_CACHE_TIMEOUT)
    return rules


def record_original_channel_access(request: Any, user: Any, model_label: str | None = None) -> None:
    """原文通道（``?mask=false`` + 对该菜单有更新权限）访问审计。

    记录「谁、在什么路径、看了哪个模型的原文」，落 **OperationLog(module=mask:original)**
    + 日志文件双通道：日志文件不可检索、不参与告警联动，敏感读必须可按人/按模型回溯
    （见 ADR / 安全审查的「脱敏原文通道」项）。每个请求只记一次（列表序列化会逐行
    调用豁免判定）；审计失败只记日志，不影响读请求本身。
    """
    if getattr(request, "_mask_original_audited", False):
        return
    try:
        request._mask_original_audited = True
    except AttributeError:  # 只读请求对象兜底
        return
    path = getattr(request, "path_info", None) or getattr(request, "path", None)
    logger.warning(
        "mask original channel accessed. user:%s path:%s model:%s",
        getattr(user, "pk", None),
        path,
        model_label or "*",
    )
    try:
        from audit.models import OperationLog

        OperationLog.objects.create(
            module=ORIGINAL_CHANNEL_MODULE,
            path=path or "",
            method=getattr(request, "method", "") or "",
            object_pk=model_label or "*",
            status_code=API_SUCCESS_CODE,
            response_result="original-channel",
            creator=user if getattr(user, "pk", None) else None,
            request_uuid=getattr(request, "request_uuid", None),
        )
    except Exception:  # noqa: BLE001 审计失败不影响读请求
        logger.warning("write mask original channel audit failed", exc_info=True)


def invalid_mask_cache(model_label: str | None = None) -> None:
    """失效脱敏缓存；model_label 缺省时清全部（规则批量变更场景）。"""
    if model_label:
        cache.delete(f"{MASK_CACHE_PREFIX}{model_label}")
        return
    data_mask_model = apps.get_model("audit", "DataMaskRule")
    models_list = list(data_mask_model.objects.values_list("model", flat=True))
    if models_list:
        cache.delete_many([f"{MASK_CACHE_PREFIX}{item}" for item in models_list])
