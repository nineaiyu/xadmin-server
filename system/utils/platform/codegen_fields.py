#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器 GUI 的字段级自定义：计划回显增强 + 覆盖收敛（codegen_gui 的域拆分）。

不引入第二套推导规则，只做引擎字段计划（ctx）的翻译与写回：

1. **计划回显**（:func:`plan_fields`）：把引擎 ctx 翻译成 GUI 字段表格的逐字段
   初始值（类型 / 是否入列 / 是否入搜索 / 默认 input_type / 文本可 icontains 等）；
2. **覆盖收敛**（:func:`apply_field_overrides`）：GUI 字段配置写回 ctx 的
   serializer_fields / table_fields / extra_kwargs / 过滤字段面——与
   ``_apply_field_selection`` 同口径，只收敛不改变推导语义。

两条框架约束（越界即报错，理由见 ADR-027 增量）：

- ``input_type`` 覆盖仅限**关联字段**：``fields_related`` 只在关系字段声明上
  pop ``input_type``；非关联字段的渲染器由序列化器字段类型决定，extra_kwargs
  传入 input_type 会在 DRF 建字段时 TypeError；
- **字典绑定**（生成 ``DictChoiceField`` 显式声明）仅限**非关联字段**：
  DictChoiceField 是 LabeledChoiceField 子类，值为普通标量而非关联对象。
"""

from common.core.modelset.input_types import DECLARED_INPUT_TYPES
from system.models.dict import DataDict

#: 关联字段开放前缀族（api-search-* 等），与词表共同构成 input_type 合法面
INPUT_TYPE_PREFIX_FAMILY = "api-"


class CodegenError(Exception):
    """GUI 生成请求不合法（字段覆盖越界 / 字典绑定无效 / 参数缺失）。"""


def input_type_allowed(value: str) -> bool:
    """input_type 合法性：封闭词表内，或开放前缀族（api-*）。"""
    return value in DECLARED_INPUT_TYPES or value.startswith(INPUT_TYPE_PREFIX_FAMILY)


def list_dict_types() -> list[dict]:
    """可绑定字典类型（启用中的类型行）：字段表格字典下拉的数据源。

    数据库不可用（未迁移 / 干跑环境）时降级为空清单，不阻断模型计划回显。
    """
    try:
        rows = DataDict.objects.filter(parent__isnull=True, is_active=True).order_by("code").values("code", "label")
    except Exception:  # noqa: BLE001 降级口径与 _model_label_pk 一致
        return []
    return [{"code": row["code"], "label": row["label"] or row["code"]} for row in rows]


def _field_map(model) -> dict:
    return {field.name: field for field in [*model._meta.fields, *model._meta.many_to_many]}


def plan_fields(ctx, model) -> list[dict]:
    """引擎字段计划 → GUI 字段表格逐字段初始值（顺序 = serializer_fields）。"""
    all_fields = _field_map(model)
    searchable = set(ctx["filter_meta_fields"])
    fields = []
    for name in ctx["serializer_fields"]:
        if name == "pk":
            fields.append(
                {
                    "name": "pk",
                    "verbose_name": "ID",
                    "type": "pk",
                    "in_table": True,
                    "in_search": False,
                    "can_search": False,
                    "required": False,
                    "is_relation": False,
                    "has_choices": False,
                    "default_input_type": "",
                    "can_filter_custom": False,
                }
            )
            continue
        field = all_fields.get(name)
        if field is None:
            fields.append(
                {
                    "name": name,
                    "verbose_name": name,
                    "type": "unknown",
                    "in_table": False,
                    "in_search": False,
                    "can_search": False,
                    "required": False,
                    "is_relation": False,
                    "has_choices": False,
                    "default_input_type": "",
                    "can_filter_custom": False,
                }
            )
            continue
        kwargs = ctx["extra_kwargs"].get(name, {})
        if "required" in kwargs:
            required = bool(kwargs["required"])
        elif field.is_relation:
            required = bool(getattr(field, "required", False))
        else:
            required = not field.blank
        fields.append(
            {
                "name": name,
                "verbose_name": str(getattr(field, "verbose_name", name) or name),
                "type": type(field).__name__,
                "in_table": name in ctx["table_fields"],
                "in_search": name in searchable,
                "can_search": name in searchable,
                "required": required,
                "is_relation": bool(field.is_relation),
                "has_choices": bool(getattr(field, "choices", None)),
                "default_input_type": str(kwargs.get("input_type") or ""),
                "can_filter_custom": name in ctx["filter_custom_fields"],
            }
        )
    return fields


def _normalize_overrides(ctx, fields_cfg) -> list[tuple[str, dict]]:
    """去重 + 字段名越界校验（含 pk / 审计字段等一切模型字段名）。"""
    known = set(_field_map(ctx["model"]))
    known.add("pk")
    overrides: dict[str, dict] = {}
    for cfg in fields_cfg:
        if not isinstance(cfg, dict):
            raise CodegenError("fields 配置项必须是对象（含 name）")
        name = str(cfg.get("name") or "").strip()
        if not name:
            continue
        if name not in known:
            raise CodegenError(f"未知字段：{name}")
        if name in overrides:
            raise CodegenError(f"字段重复配置：{name}")
        overrides[name] = cfg
    return list(overrides.items())


def _validate_dict_bind(ctx, overrides: list[tuple[str, dict]]) -> dict[str, str]:
    """字典绑定校验：非关联字段 + code 存在（启用中的类型行）；绑定值写回 ctx。"""
    bind: dict[str, str] = {}
    for name, cfg in overrides:
        if cfg.get("include") is False:
            continue
        code = str(cfg.get("dict_code") or "").strip()
        if not code:
            continue
        field = _field_map(ctx["model"]).get(name)
        if name == "pk" or (field is not None and field.is_relation):
            raise CodegenError(f"{name}：字典绑定仅支持非关联字段（关联字段的选项来自关联模型）")
        bind[name] = code
    if not bind:
        return {}
    available = {item["code"] for item in list_dict_types()}
    unknown = sorted(set(bind.values()) - available)
    if unknown:
        raise CodegenError(f"字典类型不存在或未启用：{', '.join(unknown)}")
    ctx["dict_fields"] = bind
    return bind


def apply_field_overrides(ctx, fields_cfg) -> None:
    """GUI 字段配置写回引擎 ctx（含顺序、表格列、搜索面与 extra_kwargs 覆盖）。

    收敛规则（fields 未提及的字段保持引擎推导不动）：

    - **include=False** 排除字段（pk 不可排除）；排除字段的其他配置一律忽略；
    - **顺序**：pk 恒首，其后按 fields 配置序，未提及字段按引擎序殿后；
    - **in_table / in_search**：True/False 显式生效，未提及按引擎默认（入列面
      再与保留字段面求交，保证生成物内部一致）；
    - **extra_kwargs**：label / required / read_only（DRF 原生 kwargs；
      read_only=True 时顺带清掉 required，避免二者并存的语义噪声）；
    - **input_type**：仅关联字段，词表 + api- 前缀族校验；
    - **dict_code**：仅非关联字段，生成 DictChoiceField 显式声明（ctx["dict_fields"]）。
    """
    overrides = _normalize_overrides(ctx, fields_cfg)
    excluded = {name for name, cfg in overrides if cfg.get("include") is False}
    if "pk" in excluded:
        raise CodegenError("主键 pk 不可排除")
    _validate_dict_bind(ctx, overrides)

    mentioned = {name for name, _ in overrides}
    kept_cfg = [(name, cfg) for name, cfg in overrides if name not in excluded]
    engine_order = list(ctx["serializer_fields"])
    tail = [name for name in engine_order if name not in mentioned and name != "pk"]
    ctx["serializer_fields"] = ["pk"] + [name for name, _ in kept_cfg if name != "pk"] + tail
    kept = set(ctx["serializer_fields"])

    # 表格列：pk 恒首；提及字段按显式开关（未给开关按引擎默认），未提及字段按引擎序
    engine_table = [name for name in ctx["table_fields"] if name in kept]
    table_ordered = []
    for name, cfg in kept_cfg:
        flag = cfg.get("in_table")
        if name == "pk" or flag is True or (flag is None and name in engine_table):
            table_ordered.append(name)
    unmentioned_table = [name for name in engine_table if name not in mentioned and name not in table_ordered]
    ctx["table_fields"] = table_ordered + unmentioned_table

    # 搜索面：提及字段按显式开关，未提及字段按引擎默认；icontains 自定义过滤器随面收敛
    search_ordered = [name for name, cfg in kept_cfg if name != "pk" and cfg.get("in_search") is True]
    search_tail = [name for name in ctx["filter_meta_fields"] if name not in mentioned and name in kept]
    ctx["filter_meta_fields"] = [name for name in search_ordered + search_tail if name in kept]
    custom_candidates = set(ctx["filter_custom_fields"])
    ctx["filter_custom_fields"] = [name for name in ctx["filter_meta_fields"] if name in custom_candidates]

    all_fields = _field_map(ctx["model"])
    for name, cfg in kept_cfg:
        kwargs = ctx["extra_kwargs"].setdefault(name, {})
        label = cfg.get("label")
        if isinstance(label, str) and label.strip():
            kwargs["label"] = label.strip()
        if isinstance(cfg.get("required"), bool):
            kwargs["required"] = cfg["required"]
        if isinstance(cfg.get("read_only"), bool):
            kwargs["read_only"] = cfg["read_only"]
            if cfg["read_only"]:
                kwargs.pop("required", None)
        input_type = str(cfg.get("input_type") or "").strip()
        if input_type:
            field = all_fields.get(name)
            if name == "pk" or (field is not None and not field.is_relation):
                raise CodegenError(f"{name}：input_type 覆盖仅支持关联字段（非关联字段的渲染器由字段类型决定）")
            if not input_type_allowed(input_type):
                raise CodegenError(
                    f"{name}：非法 input_type「{input_type}」（须在平台词表内或以 {INPUT_TYPE_PREFIX_FAMILY} 为前缀）"
                )
            if name in (ctx.get("dict_fields") or {}):
                raise CodegenError(f"{name}：已绑定字典，不再支持 input_type 覆盖（字典渲染器自带 input_type）")
            kwargs["input_type"] = input_type
