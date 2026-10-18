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
            written.append(write_layer_model(base, app, model, rows, manifests=manifests))
    return written


def write_layer_model(
    base: Path,
    app: str,
    model: str,
    rows: Iterable[dict[str, Any]],
    *,
    manifests: Mapping[str, SeedManifest] | None = None,
    merge: bool = False,
) -> Path:
    """把某层某模型的行写入**规范文件名**，并收敛同模型的其它文件。

    规范文件名（``menu.json`` / ``menumeta.json`` / ``modellabelfield.json`` …）是分层工具的
    唯一落点；生成器早期写过的 ``menu_<model>.json`` 等按模型拆分的文件在 manifest 里
    仍以同模型登记，会造成「同层同主键出现两份」（``SEED_PK_DUPLICATE``）。这里统一处理：

    - ``merge=False``（``seed_split`` / 导出）：``rows`` 是权威全集，直接覆盖规范文件；
    - ``merge=True``（按变更回写种子）：先与规范文件已有行按主键合并，再吸收兄弟文件里
      尚未出现在规范文件的旧行（一次性迁移），随后清空兄弟文件并从清单移除。
    """

    path = _layer_file(base, app, _filename(model))
    data = list(rows)
    siblings = _sibling_entries(path, model)
    if merge:
        data = _merge_rows(path, data)
        data = _absorb_siblings(siblings, data)
    write_rows(path, data)
    for sibling in siblings:
        write_rows(sibling, [])
    if model in SPLIT_MODELS:
        sync_manifest(
            path.parent / "manifest.json",
            model,
            kept=bool(data),
            app=app,
            manifests=manifests,
            removed={item.name for item in siblings},
        )
    return path


def _merge_rows(path: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """与现有规范文件按主键合并（原地替换、新行追加）。"""

    if not path.is_file():
        return rows
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return rows
    if not isinstance(existing, list):
        return rows
    index = {str(item.get("pk")): position for position, item in enumerate(existing)}
    for row in rows:
        pk = str(row.get("pk"))
        if pk in index:
            existing[index[pk]] = row
        else:
            existing.append(row)
    return existing


def _absorb_siblings(siblings: list[Path], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把兄弟文件里尚未出现在规范文件的行迁移过来（不覆盖同主键的新内容）。"""

    seen = {str(item.get("pk")) for item in rows}
    for sibling in siblings:
        if not sibling.is_file():
            continue
        try:
            extra = json.loads(sibling.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(extra, list):
            continue
        for item in extra:
            pk = str(item.get("pk"))
            if pk not in seen:
                rows.append(item)
                seen.add(pk)
    return rows


def _sibling_entries(path: Path, model: str) -> list[Path]:
    """同层清单里登记的「同模型、非规范文件名」的种子文件（遗留的按模型拆分产物）。"""

    manifest_path = path.parent / "manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = manifest.get("files")
    if not isinstance(entries, list):
        return []
    siblings: list[Path] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("model") != model:
            continue
        name = entry.get("name")
        if not isinstance(name, str) or name == path.name:
            continue
        candidate = path.parent / name
        if candidate.is_file():
            siblings.append(candidate)
    return siblings


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
    removed: Iterable[str] = (),
) -> None:
    """把已落地的模型写进清单 ``files``（保留 grants 等其它声明）。

    ``removed`` 是本次已收敛掉的兄弟文件名（同模型的旧拆分产物），一并从清单移除。
    """

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
    dropped = {*removed, filename}
    entries = [item for item in manifest.get("files", []) if item.get("name") not in dropped]
    if kept:
        entries.append({"name": filename, "model": model, "kind": kind, "order": order})
    manifest["files"] = sorted(entries, key=lambda item: (item.get("order", 900), item.get("name", "")))
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
