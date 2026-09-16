#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 二期 NL 查数核心工具。

四层防线（缺一不可）：
1. 禁原生 SQL——LLM 只产出受限 DSL（数据集/模式/白名单过滤/聚合/limit）；
2. 白名单校验——字段/op/metric/dataset 可见性任一越界即拒绝（LLM 输出按
   不可信输入处理，提示注入只能收敛到白名单闭包）；
3. 数据权限编译器强制过滤——执行走 execute/aggregate 管线
   （fail-closed）；
4. 试算预览 + limit 限幅 + 全程审计（OperationLog module=AI:nl_query）。
"""

import json
import re

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from system.models.dataset import Dataset
from system.utils.dataset import (
    ALLOWED_METRICS,
    ALLOWED_OPS,
    ROW_LIMIT_CAP,
    available_fields,
    get_whitelisted_model,
)

logger = get_logger(__name__)

NL_ROW_LIMIT_CAP = 200
DSL_KEYS = {"dataset", "mode", "filters", "group_by", "metric", "date_trunc", "value_field", "limit"}


def visible_datasets(user_obj) -> list:
    """当前用户可见数据集（shared ∪ 本人创建；superuser 全部）。"""
    queryset = Dataset.objects.all()
    if not getattr(user_obj, "is_superuser", False):
        queryset = queryset.filter(Q(visibility="shared") | Q(creator=user_obj))
    return list(queryset.values("pk", "name", "description", "bound_model", "columns", "config", "row_limit"))


def parse_llm_json(text: str) -> dict:
    """robust 解析 LLM 输出：剥 markdown 码栅后取首个 JSON 对象。"""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        text = text[start : end + 1] if (start >= 0 and end > start) else text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(_("The model returned malformed JSON")) from exc
    if not isinstance(payload, dict):
        raise ValidationError(_("The model returned malformed JSON"))
    unknown = set(payload) - DSL_KEYS
    if unknown:
        raise ValidationError(_("The model returned unknown DSL keys: {}").format(", ".join(sorted(unknown))))
    return payload


def _validate_filter_field(dataset: Dataset, field: str, op: str, value) -> None:
    """过滤字段必须在该数据集的模型白名单内，op 在 ALLOWED_OPS。"""
    whitelist = set(available_fields(dataset.bound_model))
    if field not in whitelist:
        raise ValidationError(_("Field {}.{} is not available for datasets").format(dataset.bound_model, field))
    if op not in ALLOWED_OPS:
        raise ValidationError(_("Filter op {} is not allowed").format(op))
    if op == "in" and not isinstance(value, (list, tuple)):
        raise ValidationError(_("Filter op in requires a list value"))
    if op == "isnull" and not isinstance(value, bool):
        raise ValidationError(_("Filter op isnull requires a boolean value"))


def validate_dsl(dsl: dict, user_obj) -> dict:
    """DSL 服务端全量校验（interpret 与 run 双侧执行）。返回规范化 DSL。"""
    if not isinstance(dsl, dict) or not dsl.get("dataset"):
        raise ValidationError(_("Invalid NL query DSL"))
    allowed_pks = {str(item["pk"]) for item in visible_datasets(user_obj)}
    if str(dsl["dataset"]) not in allowed_pks:
        raise ValidationError(_("Unknown dataset in NL query"))

    dataset = Dataset.objects.get(pk=dsl["dataset"])
    mode = dsl.get("mode") or "rows"
    if mode not in ("rows", "aggregate"):
        raise ValidationError(_("Invalid report mode"))

    filters = dsl.get("filters") or []
    if not isinstance(filters, list):
        raise ValidationError(_("Invalid dataset filters"))
    for item in filters:
        if not isinstance(item, dict):
            raise ValidationError(_("Invalid dataset filters"))
        _validate_filter_field(dataset, str(item.get("field") or ""), str(item.get("op") or ""), item.get("value"))

    normalized = {
        "dataset": str(dataset.pk),
        "mode": mode,
        "filters": [{"field": str(i["field"]), "op": i["op"], "value": i.get("value")} for i in filters],
        "limit": min(int(dsl.get("limit") or 100), ROW_LIMIT_CAP, NL_ROW_LIMIT_CAP, dataset.row_limit or 100),
    }
    if mode == "aggregate":
        columns = [str(col) for col in (dataset.columns or [])]
        group_by = str(dsl.get("group_by") or "")
        metric = dsl.get("metric") or "count"
        if metric not in ALLOWED_METRICS:
            raise ValidationError(_("Metric {} is not allowed").format(metric))
        if not group_by or group_by not in columns:
            raise ValidationError(_("Field {}.{} is not available for datasets").format(dataset.bound_model, group_by))
        normalized.update(
            {
                "group_by": group_by,
                "metric": metric,
                "date_trunc": dsl.get("date_trunc") if dsl.get("date_trunc") in ("day", "month") else "",
                "value_field": str(dsl.get("value_field") or ""),
            }
        )
        # sum/avg 的取值字段须在白名单（数值校验由 aggregate 执行层兜底）
        if normalized["metric"] in ("sum", "avg"):
            if normalized["value_field"] not in set(available_fields(dataset.bound_model)):
                raise ValidationError(
                    _("Field {}.{} is not available for datasets").format(
                        dataset.bound_model, normalized["value_field"]
                    )
                )
    else:
        # 行模式：校验绑定模型在白名单内（查询列 = 数据集列白名单）
        get_whitelisted_model(dataset.bound_model)
        if not dataset.columns:
            raise ValidationError(_("Dataset columns cannot be empty"))
    return normalized


def build_interpret_prompt(question: str, datasets: list) -> list:
    """构造 interpret 提示词：可见数据集清单 + DSL schema + 仅输出 JSON 约束。"""
    catalog = [
        {
            "dataset": str(item["pk"]),
            "name": item["name"],
            "description": item["description"],
            "columns": item["columns"],
        }
        for item in datasets
    ]
    schema = (
        '{"dataset": "<pk>", "mode": "rows|aggregate", '
        '"filters": [{"field": "<column>", "op": "exact|in|gte|gt|lte|lt|contains|startswith|isnull", "value": <value>}], '
        '"group_by": "<column>", "metric": "count|sum|avg", "date_trunc": "day|month", '
        '"value_field": "<numeric column>", "limit": 100}'
    )
    system = str(
        _(
            "You translate the user's question into a constrained dataset query DSL. "
            "You MUST pick the dataset only from the provided catalog and fields only from "
            "the dataset columns. Output ONLY a JSON object matching the schema, no prose. "
            "Use mode=aggregate with group_by/metric for statistical questions, mode=rows "
            "for record lists. Unknown values must be answered with an empty filters list."
        )
    )
    user = "{}\n\n{}\n\n---\n{}".format(
        json.dumps(catalog, ensure_ascii=False),
        str(_("DSL schema: {}").format(schema)),
        str(_("Question: {}").format(question)),
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def audit_nl_query(
    user_obj, action: str, question: str, dsl: dict, rows: int = None, error: str = "", usage: dict = None
):
    """NL 查数语义审计：落 OperationLog(module=AI:nl_query, auth_type=ai)。

    usage：LLM 供应商返回的 token 用量（成本维度观测，缺省不写）。
    """
    from system.models import OperationLog

    try:
        OperationLog.objects.create(
            module="AI:nl_query",
            object_pk=str(user_obj.pk),
            auth_type=OperationLog.AuthType.AI,
            status_code=1000 if error == "" else 1001,
            response_code=1000 if error == "" else 1001,
            changes=json.dumps(
                {
                    "action": action,
                    "question": (question or "")[:120],
                    "dsl": dsl,
                    "rows": rows,
                    "error": error,
                    **({"usage": usage} if usage else {}),
                },
                ensure_ascii=False,
                default=str,
            )[:4096],
        )
    except Exception:  # noqa: BLE001 审计失败不影响业务
        logger.warning("write NL query audit failed", exc_info=True)
