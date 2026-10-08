#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：外部模板加载与占位填充。

模板位于本包 ``templates/`` 目录，按 ``Path(__file__).parent`` 定位（随包分发）。
约定：

- 模板文件按「行」组织，与渲染器原先的 ``lines=[...]`` 逐行对应，行以 ``\\n`` 连接；
- ``[[name]]`` 为占位符：**整行占位**替换为该键的行块（``list[str]``，可为空，
  空块不产生任何行）；**行内**出现时按标量替换（``str(value)``）；
- 不使用 ``str.format`` / Django 模板语法：产物含大量 ``{}``（Python / JS）与
  ``{{ }}``（Vue 模板），会被误解析。

JSON 形态模板（如菜单种子的固定字段脚手架）用 :func:`load_json_template` 读取，
``json.loads`` 保持键序，键序即产物键序。
"""

import json
import re
from pathlib import Path
from typing import Any

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_PLACEHOLDER = re.compile(r"\[\[([A-Za-z_][A-Za-z0-9_]*)\]\]")


def load_template(name: str) -> str:
    """读取模板文本（``name`` 为 ``templates/`` 下的文件名）。"""
    path = TEMPLATES_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"代码生成模板缺失：{path}")
    return path.read_text(encoding="utf-8")


def load_json_template(name: str) -> Any:
    """读取 JSON 形态模板（固定字段脚手架，键序即产物键序）。"""
    return json.loads(load_template(name))


def _expand(line: str, values: dict[str, Any]) -> list[str]:
    match = _PLACEHOLDER.fullmatch(line)
    if match:
        block = values[match.group(1)]
        if isinstance(block, str):
            return [block]
        return [str(item) for item in block]
    return [_PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), line)]


def render_template(name: str, values: dict[str, Any]) -> str:
    """加载 ``name`` 模板并按 ``values`` 填充，返回渲染文本。

    ``values`` 的键须与模板中的占位符一一对应；缺键直接 KeyError（不静默丢行）。
    """

    lines: list[str] = []
    for line in load_template(name).split("\n"):
        lines.extend(_expand(line, values))
    return "\n".join(lines)
