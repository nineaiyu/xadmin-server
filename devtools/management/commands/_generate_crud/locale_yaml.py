#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：前端 locale YAML 的幂等合并。

只做「按顶层块插入/替换」，不整体重排 YAML——``locales/zh-CN.yaml`` 是人工维护的
大文件，整文件 dump 会丢注释与既有格式。生成的键都带模型维度（``menus.<Component>``
与 ``<localeName>.<field>``），重复生成时原地替换，不产生重复顶层键。
"""

import json
from typing import Any


def _scalar(value: Any) -> str:
    """YAML 标量：JSON 双引号字符串是合法 YAML，避免中文/冒号/井号的转义分支。"""
    return json.dumps(str(value), ensure_ascii=False)


def _top_level_end(lines: list[str], start: int) -> int:
    """返回从 start 开始的顶层块结束行号（下一个非空、非注释且无缩进的行）。"""
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            return index
    return len(lines)


def _find_block(lines: list[str], key: str) -> tuple[int, int] | None:
    for index, line in enumerate(lines):
        if line.rstrip() == f"{key}:":
            return index, _top_level_end(lines, index)
    return None


def _set_menu_title(lines: list[str], menu_key: str, menu_title: str) -> None:
    block = _find_block(lines, "menus")
    entry = f"  {menu_key}: {_scalar(menu_title)}"
    if block is None:
        lines[0:0] = ["menus:", entry, ""]
        return
    start, end = block
    for index in range(start + 1, end):
        if lines[index].startswith(f"  {menu_key}:"):
            lines[index] = entry
            return
    # 插到块尾的最后一个非空行之后，保持既有缩进与空行
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, entry)


def _replace_section(lines: list[str], section: str, labels: dict[str, str]) -> None:
    rendered = [f"{section}:"] + [f"  {name}: {_scalar(label)}" for name, label in labels.items()]
    block = _find_block(lines, section)
    if block is not None:
        start, end = block
        lines[start:end] = rendered
        return
    if lines and lines[-1].strip():
        lines.append("")
    lines.extend(rendered)


def merge_locale_yaml(
    text: str,
    *,
    menu_key: str,
    menu_title: str,
    section: str,
    labels: dict[str, str],
) -> str:
    """把菜单标题与字段词条合并进 locale 文本（幂等）。"""
    lines = text.splitlines()
    _set_menu_title(lines, menu_key, menu_title)
    _replace_section(lines, section, labels)
    return "\n".join(lines).rstrip("\n") + "\n"
