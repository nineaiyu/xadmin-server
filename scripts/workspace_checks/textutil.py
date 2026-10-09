# -*- coding: utf-8 -*-
"""跨仓一致性校验共用的纯文本解析工具（只读，无副作用）。

这些解析器与各仓库既有守护脚本/守护测试同口径，工作区脚本复用同一规则，
避免「工作区校验」与「单仓门禁」因解析差异给出不同结论。
"""

from __future__ import annotations

import ast
import json
import re

NGINX_CSP_RE = re.compile(r'add_header\s+Content-Security-Policy(?:-Report-Only)?\s+"([^"]+)"')
MJS_CSP_RE = re.compile(r"const\s+CSP\s*=\s*\[(.*?)\]\.join", re.S)
_POLICY_STRING_RE = re.compile(r'"([^"]+)"')
_YAML_KEY_RE = re.compile(r'^(\s*)([A-Za-z0-9_."]+):(\s.*)?$')
_PY_VERSION_RE = re.compile(r'^VERSION\s*=\s*["\']([^"\']+)["\']', re.M)
_DEMO_VERSION_RE = re.compile(r"VERSION=([^\s`\"']+)")


def parse_csp_directives(text: str) -> dict:
    """从 ``_CSP_DIRECTIVES = {...}`` 字面量解析 CSP 指令（键 → 值元组）。

    非字面量（含变量引用）时抛 ValueError——解析失败必须显式失败，不得静默跳过。
    """
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_CSP_DIRECTIVES":
                try:
                    value = ast.literal_eval(node.value)
                except ValueError as exc:  # pragma: no cover - 防御分支
                    raise ValueError(f"_CSP_DIRECTIVES 非字面量：{exc}") from exc
                if not isinstance(value, dict):
                    raise ValueError("_CSP_DIRECTIVES 不是字典字面量")
                return value
    raise ValueError("未找到 _CSP_DIRECTIVES 定义")


def csp_expectation(directives: dict) -> set:
    """归一化为「指令 值1 值2」字符串集合（与既有 CSP 同步守护同口径）。"""
    return {f"{key} {' '.join(values)}" for key, values in directives.items()}


def split_policy(policy: str) -> set:
    """按 ``;`` 切分策略串并剔除 report-uri 段（三处写法不同但语义一致）。"""
    items = set()
    for part in policy.split(";"):
        part = part.strip()
        if not part or part.startswith("report-uri"):
            continue
        items.add(part)
    return items


def parse_nginx_csp(text: str) -> tuple[str, int] | None:
    """返回 (策略串, 行号)；未找到强制/报告头时返回 None。"""
    match = NGINX_CSP_RE.search(text)
    if not match:
        return None
    line = text[: match.start()].count("\n") + 1
    return match.group(1), line


def parse_mjs_csp(text: str) -> tuple[str, int] | None:
    """解析 ``const CSP = [...].join(...)`` 串；未找到时返回 None。"""
    match = MJS_CSP_RE.search(text)
    if not match:
        return None
    policy = "; ".join(_POLICY_STRING_RE.findall(match.group(1)))
    line = text[: match.start()].count("\n") + 1
    return policy, line


def parse_yaml_keys(text: str) -> set:
    """解析 yaml 文本的嵌套 key 路径集合（缩进栈，与前端词条门禁同口径）。"""
    keys = set()
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        match = _YAML_KEY_RE.match(line)
        if not match:
            continue
        indent = len(match.group(1))
        name = match.group(2).strip("\"'")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        keys.add(".".join([*[item[1] for item in stack], name]))
        stack.append((indent, name))
    return keys


def parse_python_version(text: str) -> str | None:
    """解析 ``server/const.py`` 的 ``VERSION = "x.y.z"``。"""
    match = _PY_VERSION_RE.search(text)
    return match.group(1) if match else None


def parse_json_version(text: str) -> str | None:
    """解析 package.json 的 version 字段（JSON 解析，失败返回 None）。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    version = data.get("version")
    return str(version) if version else None


def parse_shell_env_version(text: str) -> str | None:
    """解析 ``VERSION=...`` 形态（static.env / demo.md 的一键安装串）。"""
    match = _DEMO_VERSION_RE.search(text)
    return match.group(1) if match else None


def normalize_version(version: str | None) -> str | None:
    """归一化版本串：去首尾空白与可选前导 ``v``。"""
    if version is None:
        return None
    return version.strip().lstrip("v") or None
