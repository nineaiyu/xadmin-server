#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导入列映射纯函数（同步导入 / 导入校验 / 异步导入三入口共用）。

设计约束（见 docs/superpowers/plans/2026-09-10-n3-business-features-phase5.md F1）：

- 本模块只做「表头字符串 ↔ 序列化字段名」的归一与映射，**不触碰 DB、请求与 DRF 视图**，
  保证三条链路共用同一份实现（历史教训：同一口径各写一份必然漂移）；
- 映射是「原始表头 → 目标字段」的显式字典，**不做模糊匹配/语义推断**（错配比不匹配更危险），
  自动候选仅用于给前端下拉提供默认值，必须由用户确认。
"""

import re

# 前端「忽略该列」选项 / 显式空值 的等价占位：命中即视为不导入该列
IGNORE_COLUMN = "__ignore__"
IGNORE_TOKENS = {"-", IGNORE_COLUMN}

# 与 BaseFileParser.obj_pattern 同源的旧格式 `名称(pk)`：括号内即字段名
_PAREN_PATTERN = re.compile(r"^(.+)\(([a-z0-9_-]+)\)$")


def normalize_header(header):
    """表头归一化：去空白与星号标记、提取 `名称(pk)` 内的字段名、统一大小写与分隔符。"""
    if header is None:
        header = ""
    elif not isinstance(header, str):
        header = str(header)
    header = header.strip().strip("*").strip()
    matched = _PAREN_PATTERN.match(header)
    if matched:
        header = matched.group(2)
    return header.strip().lower().replace(" ", "_").replace("-", "_")


def _is_ignore_value(value):
    """映射值是否为「显式忽略该列」；非字符串值一律按非法（不忽略）处理。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip() in IGNORE_TOKENS
    return False


def iter_writable_fields(fields):
    """按导入口径过滤可写字段（与 BaseFileParser.convert_to_field_names 一致）。

    ``id`` / ``pk`` 例外保留：更新导入靠它们定位记录。
    """
    for name, field in fields.items():
        if getattr(field, "read_only", False) and name not in ("id", "pk"):
            continue
        yield name, field


def build_field_index(fields):
    """构造「归一化表头 → 序列化字段名」索引（字段名与 label 双向可命中）。"""
    index = {}
    for name, field in iter_writable_fields(fields):
        index.setdefault(normalize_header(name), name)
        label = getattr(field, "label", None)
        if label:
            index.setdefault(normalize_header(str(label)), name)
    return index


def writable_field_options(fields):
    """目标字段下拉选项：[{value: 字段名, label: 展示名}]（供列映射步骤使用）。"""
    options = []
    for name, field in iter_writable_fields(fields):
        label = getattr(field, "label", None)
        options.append({"value": name, "label": str(label) if label else name})
    return options


def first_column_candidates(headers, fields):
    """为每个表头给出「归一化等名」候选字段名（仅提示，无候选返回空串）。"""
    index = build_field_index(fields)
    return [index.get(normalize_header(header), "") for header in headers]


def apply_column_mapping(headers, mapping, ignore_unknown=True):
    """按映射替换表头，返回 ``(resolved, unmatched)``。

    - ``mapping``：{原始表头: 目标字段名}，键匹配走 :func:`normalize_header`；
      值命中 :data:`IGNORE_TOKENS` / 空值 表示显式忽略该列（解析阶段丢弃）；
    - 命中映射的列 → 采用映射值；
    - 未命中映射的列 → ``ignore_unknown`` 为真时置空（丢弃），否则原样保留
      （仍可按字段名/label 等名匹配）；两种情况都记入 ``unmatched`` 供上层给出可读提示。
    """
    normalized_mapping = {}
    for key, value in (mapping or {}).items():
        if not isinstance(key, str):
            continue
        normalized_mapping[normalize_header(key)] = "" if _is_ignore_value(value) else str(value).strip()
    resolved, unmatched = [], []
    for header in headers:
        key = normalize_header(header)
        if key in normalized_mapping:
            resolved.append(normalized_mapping[key])
        else:
            unmatched.append(header)
            resolved.append("" if ignore_unknown else header)
    return resolved, unmatched


def resolve_headers(headers, mapping, fields, ignore_unknown=True):
    """映射 + 字段解析一步到位（文件解析器入口）。

    返回 ``(field_names, unmatched)``：``field_names`` 与 ``headers`` 等长，
    无法解析到字段的列一律置空——解析阶段按空字段名丢弃该列，与历史
    「表头未匹配即忽略」的行为保持一致。
    """
    resolved, unmatched = apply_column_mapping(headers, mapping, ignore_unknown)
    index = build_field_index(fields)
    field_names = [index.get(normalize_header(item), "") if item else "" for item in resolved]
    return field_names, unmatched
