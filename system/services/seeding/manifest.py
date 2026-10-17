#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""种子清单（``manifest.json``）的读取与结构校验。

清单只声明「哪些文件属于这一层、按什么顺序装、类别是什么、依赖谁」，不承载行数据。
解析失败返回问题清单而不抛异常：一层清单写坏不应让其它层无法装载或无法诊断。
"""

import json
from pathlib import Path
from typing import Any

from . import paths
from .types import (
    GRANTS_MODEL,
    KIND_CONTRACT,
    KIND_SAMPLE,
    KINDS,
    LEVEL_ERROR,
    LEVEL_WARNING,
    Problem,
    SeedFileSpec,
    SeedManifest,
)

MANIFEST_VERSION = 1
DEFAULT_ORDER = 10


def _as_str_tuple(value: Any, field: str, problems: list[Problem]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", f"{field} 必须是字符串数组"))
        return ()
    return tuple(value)


def _spec_name_ok(name: Any, problems: list[Problem], *, sample: bool) -> str | None:
    if not isinstance(name, str) or not name:
        problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", "种子文件名必须是非空字符串"))
        return None
    if name.startswith("/") or ".." in Path(name).parts:
        problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", f"种子文件必须是层内相对路径：{name}"))
        return None
    if not name.endswith(".json"):
        problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", f"种子文件必须是 .json：{name}"))
        return None
    if sample and not name.startswith("sample/"):
        problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", f"示例种子必须放在 sample/ 下：{name}"))
        return None
    return name


def _build_specs(
    app: str,
    base_dir: Path,
    entries: Any,
    problems: list[Problem],
    *,
    sample: bool,
) -> tuple[SeedFileSpec, ...]:
    """把清单里的文件条目解析成 :class:`SeedFileSpec`（保持声明顺序）。

    路径以**清单所在目录**为基准解析：平台层清单在 ``loadjson/``，app 层在 ``seeds/``，
    两者同构，解析不依赖应用是否已注册（便于校验独立清单）。
    """

    if entries is None:
        return ()
    if not isinstance(entries, list):
        problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", "files / sample 必须是数组"))
        return ()

    specs: list[SeedFileSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", f"第 {index + 1} 个文件条目不是对象"))
            continue
        name = _spec_name_ok(entry.get("name"), problems, sample=sample)
        model = entry.get("model")
        if name is None:
            continue
        if name in seen:
            problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", f"重复声明的种子文件：{name}"))
            continue
        seen.add(name)
        if not isinstance(model, str) or "." not in model:
            problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", f"{name} 的 model 必须是 app_label.model"))
            continue

        kind = KIND_SAMPLE if sample else entry.get("kind", KIND_CONTRACT)
        if not sample and kind not in KINDS:
            problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", f"{name} 的 kind 不在 {KINDS} 内：{kind}"))
            continue
        if not sample and kind == KIND_SAMPLE:
            problems.append(
                Problem(LEVEL_WARNING, "MANIFEST_INVALID", f"{name} 声明为 sample，应移入 sample 列表（已按示例处理）")
            )
            kind = KIND_SAMPLE

        order = entry.get("order", DEFAULT_ORDER)
        if not isinstance(order, int):
            problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", f"{name} 的 order 必须是整数"))
            continue

        specs.append(SeedFileSpec(app=app, model=model, path=base_dir / name, kind=kind, order=order))
    return tuple(specs)


def _build_grant_spec(
    app: str,
    base_dir: Path,
    entry: Any,
    problems: list[Problem],
) -> SeedFileSpec | None:
    """解析可选的 ``grants`` 声明（字符串或 ``{name, order}``）。

    授权声明不是 fixture：它描述「本层的菜单被哪些角色拥有」，装载后按并集应用。
    """

    if entry is None:
        return None
    name: Any
    order: Any
    if isinstance(entry, str):
        name, order = entry, 900
    elif isinstance(entry, dict):
        name, order = entry.get("name"), entry.get("order", 900)
    else:
        problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", "grants 必须是字符串或对象"))
        return None
    if not isinstance(order, int):
        problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", "grants.order 必须是整数"))
        return None
    valid_name = _spec_name_ok(name, problems, sample=False)
    if valid_name is None:
        return None
    return SeedFileSpec(app=app, model=GRANTS_MODEL, path=base_dir / valid_name, kind=KIND_CONTRACT, order=order)


def parse_manifest(
    path: Path,
    *,
    expected_app: str | None = None,
) -> tuple[SeedManifest | None, list[Problem]]:
    """读取并校验一个清单文件，返回 ``(manifest, problems)``。

    ``expected_app`` 用于校验 ``app`` 字段与所在目录一致（app 层传 app label，
    平台层传 :data:`PLATFORM`），防止清单被复制到错误目录后静默生效。
    """

    problems: list[Problem] = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, problems
    except (OSError, json.JSONDecodeError) as exc:
        return None, [Problem(LEVEL_ERROR, "MANIFEST_UNREADABLE", f"清单无法解析：{exc}", file=path)]

    if not isinstance(raw, dict):
        return None, [Problem(LEVEL_ERROR, "MANIFEST_INVALID", "清单必须是 JSON 对象", file=path)]

    version = raw.get("version")
    if version != MANIFEST_VERSION:
        problems.append(
            Problem(LEVEL_ERROR, "MANIFEST_INVALID", f"version 必须为 {MANIFEST_VERSION}，实际 {version!r}", file=path)
        )

    app = raw.get("app")
    if not isinstance(app, str) or not app:
        problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", "app 必须是非空字符串", file=path))
        return None, problems
    if expected_app is not None and app != expected_app:
        problems.append(
            Problem(LEVEL_ERROR, "MANIFEST_APP_MISMATCH", f"app={app!r} 与所在层 {expected_app!r} 不一致", file=path)
        )

    if "files" not in raw and "sample" not in raw:
        problems.append(Problem(LEVEL_ERROR, "MANIFEST_INVALID", "至少声明 files 或 sample 之一", file=path))

    files = _build_specs(app, path.parent, raw.get("files"), problems, sample=False)
    sample = _build_specs(app, path.parent, raw.get("sample"), problems, sample=True)

    manifest = SeedManifest(
        app=app,
        depends=_as_str_tuple(raw.get("depends"), "depends", problems),
        url_prefixes=_as_str_tuple(raw.get("url_prefixes"), "url_prefixes", problems),
        menu_roots=_as_str_tuple(raw.get("menu_roots"), "menu_roots", problems),
        config_keys=_as_str_tuple(raw.get("config_keys"), "config_keys", problems),
        dict_codes=_as_str_tuple(raw.get("dict_codes"), "dict_codes", problems),
        files=files,
        sample=sample,
        grants=_build_grant_spec(app, path.parent, raw.get("grants"), problems),
    )
    return manifest, _attach_file(problems, path)


def _attach_file(problems: list[Problem], path: Path) -> list[Problem]:
    """给未带定位的问题补上清单路径（命令行输出需要能直接跳转）。"""

    return [
        Problem(problem.level, problem.code, problem.message, file=problem.file or path, row_index=problem.row_index)
        for problem in problems
    ]


def platform_manifest_path(root: Path | str | None = None) -> Path:
    """平台层清单路径（供发现逻辑与测试共用）。"""

    return paths.platform_file(paths.MANIFEST_NAME, root)
