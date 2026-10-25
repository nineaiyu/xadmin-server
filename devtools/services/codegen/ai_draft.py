#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""代码生成器「AI 字段草案」（提案 P4 二阶段）：自然语言 → 字段覆盖草案。

原则（与提案一致）：**AI 产物过人审，不直写代码**。AI 只产出
``CodegenFieldOverride[]`` 形态的草案（与 GUI 字段表格同语义），服务端按
``apply_field_overrides`` 同一套约束逐项过滤——非法项剥成 warnings 而非整体
拒绝；草案经前端人审采纳后走既有 preview / download 管线，本模块不渲染
任何产物、不写仓库，服务端不新增第二套字段语义。

调用面与 AI 域既有链路同构：``structured_chat_client`` + ``tracked_chat``
（feature=codegen 记账、不落会话表）；模型字段目录经 ``annotate_reference``
包裹（注入防护）。开关 ``AI_CODEGEN_DRAFT_ENABLED`` 默认关（LLM 成本增量）。
"""

from typing import Any

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from common.utils.ai_parse import extract_json_object
from devtools.services.codegen.fields import (
    FIELD_CLASS_OPTIONS,
    CodegenError,
    input_type_allowed,
)
from devtools.services.codegen.gui import model_plan

# 草案条目数上限：超出部分丢弃并告警（防模型复读机输出刷屏）
MAX_DRAFT_FIELDS = 64


def draft_enabled() -> bool:
    """AI 字段草案开关（AI 配置页热更新，默认关）。"""
    return bool(getattr(settings, "AI_CODEGEN_DRAFT_ENABLED", False))


def _reference_text(plan: dict[str, Any]) -> str:
    """LLM 引用目录：字段清单（类型 / 约束）+ 可绑定字典 + 词表说明。"""
    lines = ["Fields (name | type | constraints):"]
    for row in plan["fields"]:
        tags = []
        if row.get("is_relation"):
            tags.append("relation")
        if row.get("has_choices"):
            tags.append("choices")
        if row.get("can_field_class"):
            tags.append("field_class:" + "|".join(FIELD_CLASS_OPTIONS))
        lines.append(f"- {row['name']} | {row['type']} | {', '.join(tags) or '-'}")
    dict_types = ", ".join(item["code"] for item in plan["dict_types"]) or "(none)"
    lines.append(f"Bindable dict codes: {dict_types}")
    lines.append(
        "Relation fields may set input_type (platform vocab or api-* prefix); "
        "non-relation fields may set dict_code or field_class (mutually exclusive), never both."
    )
    return "\n".join(lines)


def _messages(reference: str, description: str) -> list[dict[str, str]]:
    system = (
        "You draft field overrides for a CRUD code generator. Reply with ONE JSON object only, "
        'no markdown fences: {"fields": [{"name": ..., "include": bool, "label": str, '
        '"required": bool, "read_only": bool, "in_table": bool, "in_search": bool, '
        '"input_type": str, "dict_code": str, "field_class": str}], "summary": str}. '
        "Rules: use ONLY field names from the catalog (never invent fields); mention ONLY "
        "fields that need changes; input_type only for relation fields; dict_code / field_class "
        "only for non-relation fields and mutually exclusive; pk cannot be excluded; "
        "summary is one short sentence in the site language (Chinese)."
    )
    user = f"Field catalog:\n{reference}\n\nUser request:\n{description}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_draft(raw: str, plan: dict[str, Any]) -> dict[str, Any]:
    """LLM 原始输出 → 草案 + 告警（非法项剔除为 warnings，不整体拒绝）。

    校验口径与 ``apply_field_overrides`` 同源（字段名闭包 / input_type 仅关联
    字段 / dict_code 与 field_class 仅非关联字段且互斥），保证采纳后的草案
    一定能通过既有写回管线。
    """
    try:
        data = extract_json_object(raw)
    except Exception as exc:  # noqa: BLE001 解析失败即草案无效（可读原因透出）
        raise CodegenError(str(_("AI draft is not valid JSON: {}")).format(str(exc)[:120])) from exc

    raw_fields = data.get("fields")
    if not isinstance(raw_fields, list):
        raise CodegenError(str(_("AI draft must contain a fields array")))

    by_name = {row["name"]: row for row in plan["fields"]}
    dict_codes = {item["code"] for item in plan["dict_types"]}
    warnings: list[str] = []
    draft: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_fields[:MAX_DRAFT_FIELDS]:
        if not isinstance(item, dict):
            warnings.append(str(_("Skipped a non-object draft entry")))
            continue
        name = str(item.get("name") or "").strip()
        if not name or name not in by_name:
            warnings.append(str(_("Unknown field skipped: {name}")).format(name=name or "-"))
            continue
        if name in seen:
            warnings.append(str(_("Duplicate field config skipped: {name}")).format(name=name))
            continue
        meta = by_name[name]
        seen.add(name)
        cfg: dict[str, Any] = {"name": name}
        for key in ("include", "required", "read_only", "in_table", "in_search"):
            if isinstance(item.get(key), bool):
                cfg[key] = item[key]
        if name == "pk" and cfg.get("include") is False:
            cfg.pop("include")
            warnings.append(str(_("pk cannot be excluded; flag dropped")))
        label = str(item.get("label") or "").strip()
        if label:
            cfg["label"] = label
        input_type = str(item.get("input_type") or "").strip()
        if input_type:
            if not meta["is_relation"]:
                warnings.append(str(_("{name}: input_type only applies to relation fields; dropped")).format(name=name))
            elif not input_type_allowed(input_type):
                warnings.append(
                    str(_("{name}: invalid input_type {value} dropped")).format(name=name, value=input_type)
                )
            else:
                cfg["input_type"] = input_type
        dict_code = str(item.get("dict_code") or "").strip()
        field_class = str(item.get("field_class") or "").strip()
        if dict_code and meta["is_relation"]:
            warnings.append(str(_("{name}: dict bind only applies to non-relation fields; dropped")).format(name=name))
            dict_code = ""
        if field_class and meta["is_relation"]:
            warnings.append(
                str(_("{name}: field_class only applies to non-relation fields; dropped")).format(name=name)
            )
            field_class = ""
        if dict_code and dict_code not in dict_codes:
            warnings.append(str(_("{name}: unknown dict code {value} dropped")).format(name=name, value=dict_code))
            dict_code = ""
        if field_class and field_class not in FIELD_CLASS_OPTIONS:
            warnings.append(str(_("{name}: unknown field class {value} dropped")).format(name=name, value=field_class))
            field_class = ""
        if dict_code and field_class:
            # 互斥：保留字典绑定，字段类剔除（apply_field_overrides 二者并存会整体报错）
            warnings.append(
                str(_("{name}: dict_code and field_class are exclusive; field_class dropped")).format(name=name)
            )
            field_class = ""
        if dict_code:
            cfg["dict_code"] = dict_code
        if field_class:
            cfg["field_class"] = field_class
        draft.append(cfg)
    if len(raw_fields) > MAX_DRAFT_FIELDS:
        warnings.append(str(_("Draft truncated to {count} fields")).format(count=MAX_DRAFT_FIELDS))
    return {"fields": draft, "warnings": warnings, "summary": str(data.get("summary") or "")[:500]}


def generate_ai_draft(user: Any, label: str, description: str) -> dict[str, Any]:
    """自然语言 → 字段覆盖草案（开关 + 配置双闸；用量记账 feature=codegen）。"""
    if not draft_enabled():
        raise CodegenError(str(_("AI field draft is disabled (enable it on the AI settings page)")))
    if not description.strip():
        raise CodegenError(str(_("Description is required")))
    from ai.models.ai import AiUsageRecord
    from ai.services.assistant.ai_config import is_configured, structured_chat_client
    from ai.services.assistant.ai_guard import annotate_reference
    from ai.services.assistant.ai_usage import tracked_chat

    if not is_configured():
        raise CodegenError(str(_("AI provider is not configured")))

    plan = model_plan(label)  # 未知模型 → CodegenError（与字段计划端点同口径）
    reference, _hits = annotate_reference(
        _reference_text(plan), label=f"codegen:{label}", user=user, kind="codegen_catalog"
    )
    client, max_tokens = structured_chat_client()
    raw = tracked_chat(
        user,
        str(AiUsageRecord.Feature.CODEGEN),
        _messages(str(reference), description.strip()),
        client=client,
        max_tokens=max_tokens,
    )
    result = parse_draft(raw, plan)
    result["model"] = label
    return result
