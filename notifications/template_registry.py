#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通知模板覆盖的渲染与校验。

- ``apply_override``：按消息类型套用 DB 覆盖（subject / body 分别可选），
  在渠道渲染收口调用，未配置零行为变化；
- ``validate_template``：保存时校验模板语法与变量白名单（不开放任意模板逻辑）；
- 覆盖行读取带 60s 短缓存，保存 / 重置时主动失效。
"""

import re

from django.core.cache import cache
from django.template import Context, Engine
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

OVERRIDE_CACHE_KEY = "message_template_overrides"
OVERRIDE_CACHE_TTL = 60

# 通用可用变量（所有消息类型共享）；消息类可经 template_variables() 追加
COMMON_VARIABLES = ("subject", "message", "message_type")
VARIABLE_PATTERN = re.compile(r"{{\s*([\w.]+)\s*}}")


def get_overrides() -> dict:
    """读取启用中的覆盖行（60s 缓存）：{message_type: {"subject", "body"}}。

    数据库不可用时返回空覆盖（通知渲染回退代码默认，绝不因模板层故障丢消息；
    单测未开 db 访问的场景也走此分支）。
    """
    cached = cache.get(OVERRIDE_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        from notifications.models import MessageTemplate

        data = {
            row.message_type: {"subject": row.subject_template or "", "body": row.body_template or ""}
            for row in MessageTemplate.objects.filter(is_active=True)
            if (row.subject_template or "").strip() or (row.body_template or "").strip()
        }
    except Exception:  # noqa: BLE001 库不可用 / 未建表 / 未开 db 访问：按无覆盖处理
        return {}
    try:
        cache.set(OVERRIDE_CACHE_KEY, data, OVERRIDE_CACHE_TTL)
    except Exception:  # noqa: BLE001 缓存不可用不影响渲染
        pass
    return data


def invalidate_overrides():
    cache.delete(OVERRIDE_CACHE_KEY)


def render_template(template_text: str, context: dict) -> str:
    # 沙箱：只做变量插值（string_if_invalid 为空串、不自动转义），不注册自定义标签/过滤器
    engine = Engine(string_if_invalid="", autoescape=False)
    return engine.from_string(template_text).render(Context(context))


def apply_override(message_type, msg: dict, extra=None) -> dict:
    """套用 DB 覆盖：返回新的 {subject, message}（无覆盖时原样返回）。"""
    override = get_overrides().get(message_type)
    if not override:
        return msg
    context = {
        "subject": str(msg.get("subject") or ""),
        "message": str(msg.get("message") or ""),
        "message_type": message_type,
    }
    if extra:
        context.update({key: value for key, value in extra.items() if key not in context or not context[key]})
    result = dict(msg)
    try:
        if override.get("subject"):
            result["subject"] = render_template(override["subject"], context)
        if override.get("body"):
            result["message"] = render_template(override["body"], context)
    except Exception:  # noqa: BLE001 覆盖模板渲染异常回退默认内容（通知不能因此丢失）
        logger.warning("render message template override failed. message_type:%s", message_type, exc_info=True)
        return msg
    return result


def extract_variables(template_text: str) -> set:
    return set(VARIABLE_PATTERN.findall(str(template_text or "")))


def validate_template(template_text: str, available_variables) -> str:
    """校验模板语法与变量白名单，返回错误文案（通过返回空串）。"""
    text = str(template_text or "")
    if not text.strip():
        return ""
    try:
        Engine(string_if_invalid="", autoescape=False).from_string(text)
    except Exception as exc:  # noqa: BLE001 模板语法错误直接返回可读原因
        return str(_("Template syntax error: {}")).format(str(exc)[:120])
    unknown = sorted(extract_variables(text) - set(available_variables or []))
    if unknown:
        return str(_("Unknown template variables: {}")).format(", ".join(unknown))
    return ""
