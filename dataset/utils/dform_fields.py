#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""动态表单字段值工具（自 dataset/utils/dform.py 平移）：级联 / 用户选择 / 明细表 / 选项 / 附件归属校验。

对外的既有导入面（dataset.utils.dform）经该模块再导出保持不变。
"""

from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from dataset.utils.dform_constants import (
    DATE_RE,
    MAX_CASCADER_DEPTH,
    MAX_CASCADER_NODES,
    MAX_TEXT_LENGTH,
)


def _validate_cascader_options(key: str, options):
    """级联选项树校验：{value,label[,children]} 递归 ≤3 层、节点总数封顶。"""
    if not isinstance(options, list) or not options:
        raise ValidationError(_("Field {} requires options").format(key))
    counter = {"total": 0}

    def walk(nodes, depth):
        if depth > MAX_CASCADER_DEPTH:
            raise ValidationError(_("Field {} cascader options exceed {} levels").format(key, MAX_CASCADER_DEPTH))
        for node in nodes:
            if not isinstance(node, dict):
                raise ValidationError(_("Field {} has an invalid cascader option").format(key))
            value = node.get("value")
            # 值允许字符串或整数（布尔是 int 子类，显式排除）
            if isinstance(value, bool) or not isinstance(value, (str, int)) or str(value).strip() == "":
                raise ValidationError(_("Field {} cascader option requires a value").format(key))
            if not str(node.get("label") or "").strip():
                raise ValidationError(_("Field {} cascader option requires a label").format(key))
            counter["total"] += 1
            if counter["total"] > MAX_CASCADER_NODES:
                raise ValidationError(_("Field {} has too many cascader options").format(key))
            children = node.get("children")
            if children is not None:
                if not isinstance(children, list) or not children:
                    raise ValidationError(_("Field {} has an invalid cascader option").format(key))
                walk(children, depth + 1)

    walk(options, 1)


def _validate_user_pk(label: str, value):
    """选人控件取值：正整数用户主键（不接受布尔/字符串/浮点）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(_("Field {} must be a user id").format(label))


def _cascader_path_valid(options, path: list) -> bool:
    """级联取值必须命中选项树的叶子路径（逐段比对，值类型允许 str/int）。"""
    nodes = options
    for index, step in enumerate(path):
        node = next((item for item in nodes if item.get("value") == step), None)
        if node is None:
            return False
        last = index == len(path) - 1
        children = node.get("children")
        if last:
            return not children
        if not children:
            return False
        nodes = children
    return False


def normalize_table_row(item: dict, label: str, row) -> dict:
    """明细子表单行校验：按列定义逐列校验，未知列键拒绝，返回规范化行。"""
    if not isinstance(row, dict):
        raise ValidationError(_("Field {} rows must be objects").format(label))
    columns = item.get("columns") or []
    known = {column["key"]: column for column in columns}
    unknown = set(row) - set(known)
    if unknown:
        raise ValidationError(_("Field {} has unknown columns: {}").format(label, ", ".join(sorted(unknown))))
    normalized: dict[str, Any] = {}
    for column in columns:
        column_key = column["key"]
        column_type = column.get("type")
        value = row.get(column_key)
        if value is None or value == "":
            normalized[column_key] = None
            continue
        if column_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValidationError(_("Field {} must be numeric").format(column_key))
        elif column_type == "select":
            if value not in (column.get("options") or []):
                raise ValidationError(_("Field {} has an invalid option: {}").format(column_key, value))
        elif column_type == "date":
            if not isinstance(value, str) or not DATE_RE.match(value):
                raise ValidationError(_("Field {} must be a date").format(column_key))
        else:
            if not isinstance(value, str):
                raise ValidationError(_("Field {} must be text").format(column_key))
            if len(value) > MAX_TEXT_LENGTH:
                raise ValidationError(_("Field {} exceeds the max length {}").format(column_key, MAX_TEXT_LENGTH))
        normalized[column_key] = value
    return normalized


def field_option_values(item: dict) -> list:
    """选项型字段的合法取值集合：绑定字典时读字典项 value（5 分钟缓存，失败降级空集）。

    空集合语义 = fail-closed：字典被清空/停用时该字段不接受任何取值（提交被拒）。
    """
    dict_code = item.get("dict")
    if dict_code:
        from system.utils.platform.dict import get_dict_items

        return [row.get("value") for row in get_dict_items(str(dict_code)) if row.get("value") is not None]
    return list(item.get("options") or [])


def assert_upload_ownership(value, label, user) -> None:
    """upload 控件值归属断言：文件必须**存在且由提交人上传**（fail-closed）。

    历史实现只校验「条目是带 pk 的对象」——可以引用他人文件 pk（访问侧靠
    UploadFile 数据权限兜底，跨角色/字段权限配置下仍可能读到别人的附件）。
    提交链路统一在提交校验时批量断言；无用户上下文（种子/脚本）由调用方传 None 跳过。
    """
    from file.services import UploadFile

    pks = [str(item.get("pk") or "").strip() for item in value]
    pks = [pk for pk in pks if pk]
    if not pks:
        return
    try:
        owned = {str(pk) for pk in UploadFile.objects.filter(pk__in=pks, creator=user).values_list("pk", flat=True)}
    except Exception:  # noqa: BLE001 非法 pk 形态等：按全部不归属处理（fail-closed）
        owned = set()
    invalid = [pk for pk in pks if pk not in owned]
    if invalid:
        raise ValidationError(_("Field {} contains files that do not belong to you").format(label))
