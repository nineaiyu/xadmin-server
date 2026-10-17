#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""按归属把种子行分发到各层并落盘（导入/导出共用的唯一实现）。

两个调用方：

- ``manage.py seed_split``：把**现有种子文件**重排到各层（不连库）；
- ``manage.py dump_init_json --split-apps``：把**库里的行**导出到各层。

两者共用 :func:`bucket_rows` 与 :func:`write_buckets`，因此「归属口径」只存在一份——
否则导出与导入各写一套规则，迟早出现「导出到 A 层、导入从 B 层读」的错位。
"""

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from django.conf import settings

from .types import KIND_CONTRACT, KIND_GENERATED, PLATFORM, SeedManifest

# 可归属的六类种子：模型 → (文件名, kind, 层内顺序)。顺序复刻历史装载序
# （菜单元数据先于菜单、字段白名单先于字段树）。
SPLIT_MODELS: dict[str, tuple[str, str, int]] = {
    "system.menumeta": ("menumeta.json", KIND_CONTRACT, 10),
    "system.menu": ("menu.json", KIND_CONTRACT, 20),
    "system.systemconfig": ("systemconfig.json", KIND_CONTRACT, 30),
    "system.datadict": ("datadict.json", KIND_CONTRACT, 40),
    "system.fieldpermission": ("fieldpermission.json", KIND_CONTRACT, 50),
    "system.modellabelfield": ("modellabelfield.json", KIND_GENERATED, 60),
}

Bucket = dict[str, dict[str, list[dict[str, Any]]]]


def bucket_rows(
    rows_by_model: Mapping[str, Iterable[dict[str, Any]]],
    manifests: Mapping[str, SeedManifest],
    *,
    selected: Iterable[str] | None = None,
) -> Bucket:
    """按归属把行分到各层。

    ``selected`` 为 ``None`` 表示全量导出；给出应用集合时，**平台层共享行始终包含**，
    未选中的应用整层跳过（导出补丁而不是整库 dump）。
    """

    from .ownership import row_owners

    wanted = set(selected) if selected is not None else None
    splittable = {model: list(rows) for model, rows in rows_by_model.items() if model in SPLIT_MODELS}
    owners = row_owners(splittable, dict(manifests))

    buckets: Bucket = defaultdict(lambda: defaultdict(list))
    for model, rows in rows_by_model.items():
        if model not in SPLIT_MODELS:
            # 平台级模型（角色/部门/站点配置/登录策略/数据权限）不参与归属拆分
            buckets[PLATFORM][model] = list(rows)
            continue
        for row in rows:
            owner = owners.get(model, {}).get(str(row.get("pk") or ""), PLATFORM)
            if owner != PLATFORM and wanted is not None and owner not in wanted:
                continue
            buckets[owner][model].append(row)
    return buckets


def write_buckets(
    buckets: Bucket,
    *,
    root: Path | str | None = None,
    manifests: Mapping[str, SeedManifest] | None = None,
) -> list[Path]:
    """把分好的行写回各层文件（``root`` 供导出到自定义目录/测试使用）。"""

    base = Path(root) if root is not None else Path(settings.PROJECT_DIR)
    written: list[Path] = []
    for app in sorted(buckets):
        for model, rows in buckets[app].items():
            filename = _filename(model)
            path = _layer_file(base, app, filename)
            write_rows(path, rows)
            if model in SPLIT_MODELS:
                sync_manifest(path.parent / "manifest.json", model, kept=bool(rows), app=app, manifests=manifests)
            written.append(path)
    return written


def write_rows(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(rows), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _filename(model: str) -> str:
    if model in SPLIT_MODELS:
        return SPLIT_MODELS[model][0]
    return f"{model.split('.')[-1]}.json"


def _layer_file(base: Path, app: str, filename: str) -> Path:
    if app == PLATFORM:
        return base / "loadjson" / filename
    return base / app / "seeds" / filename


def sync_manifest(
    path: Path,
    model: str,
    *,
    kept: bool,
    app: str,
    manifests: Mapping[str, SeedManifest] | None,
) -> None:
    """把已落地的模型写进清单 ``files``（保留 grants 等其它声明）。"""

    if not path.is_file():
        template = (manifests or {}).get(app)
        if template is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "app": app,
                    "depends": list(template.depends),
                    "url_prefixes": list(template.url_prefixes),
                    "files": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    filename, kind, order = SPLIT_MODELS[model]
    entries = [item for item in manifest.get("files", []) if item.get("name") != filename]
    if kept:
        entries.append({"name": filename, "model": model, "kind": kind, "order": order})
    manifest["files"] = sorted(entries, key=lambda item: (item.get("order", 900), item.get("name", "")))
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
