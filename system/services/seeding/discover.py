#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""种子层发现：平台层（``loadjson/``）+ 各已注册 app 的 ``seeds/manifest.json``。

发现按 ``INSTALLED_APPS`` 顺序进行，因此种子面天然跟随应用注册（``XADMIN_APPS``）收窄。
平台层顺序以历史模型序为主干（见 :mod:`legacy`），清单里新增的文件追加在主干之后，
这样「未迁移任何 app」时的装载顺序与拆分前完全一致。
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from django.apps import apps as django_apps

from . import paths
from .legacy import legacy_models
from .manifest import parse_manifest, platform_manifest_path
from .types import KIND_CONTRACT, PLATFORM, Problem, SeedFileSpec, SeedManifest


@dataclass(frozen=True)
class Discovery:
    """一次发现的全部种子层。``manifests`` 保持 ``INSTALLED_APPS`` 顺序。"""

    platform: SeedManifest
    manifests: dict[str, SeedManifest]
    problems: tuple[Problem, ...] = ()


def discover(*, legacy: Sequence[Any] | None = None) -> Discovery:
    """发现平台层与全部 app 层。

    :param legacy: 平台层历史模型序；缺省取 :func:`legacy_models`（测试可传入固定序列）
    """

    models = tuple(legacy) if legacy is not None else legacy_models()
    platform_manifest, problems = _load_platform_manifest()
    platform = replace(platform_manifest, files=_assemble_platform(platform_manifest, models))

    manifests: dict[str, SeedManifest] = {}
    for config in django_apps.get_app_configs():
        manifest_path = paths.manifest_file(config.label)
        if not manifest_path.is_file():
            continue
        manifest, issues = parse_manifest(manifest_path, expected_app=config.label)
        problems.extend(issues)
        if manifest is not None:
            manifests[config.label] = manifest
    return Discovery(platform=platform, manifests=manifests, problems=tuple(problems))


def _load_platform_manifest() -> tuple[SeedManifest, list[Problem]]:
    """读取平台层清单；缺失时退化为「全部文件都属平台层」的历史形态。"""

    path = platform_manifest_path()
    if not path.is_file():
        return SeedManifest(app=PLATFORM), []
    manifest, problems = parse_manifest(path, expected_app=PLATFORM)
    return (manifest or SeedManifest(app=PLATFORM)), problems


def _assemble_platform(manifest: SeedManifest, models: Sequence[Any]) -> tuple[SeedFileSpec, ...]:
    """平台层文件顺序：历史模型序为主干，清单声明提供元数据，未在主干内的文件追加在后。"""

    declared = {spec.name: spec for spec in manifest.files}
    specs: list[SeedFileSpec] = []
    used: set[str] = set()

    for index, model in enumerate(models):
        name = f"{model._meta.model_name}.json"
        path = paths.platform_file(name)
        if not path.is_file():
            continue
        base = declared.get(name)
        specs.append(
            SeedFileSpec(
                app=PLATFORM,
                model=base.model if base is not None else model._meta.label_lower,
                path=path,
                kind=base.kind if base is not None else KIND_CONTRACT,
                order=base.order if base is not None else index * 10,
            )
        )
        used.add(name)

    for spec in sorted(manifest.files, key=lambda item: (item.order, item.name)):
        if spec.name in used or not spec.path.is_file():
            continue
        specs.append(spec)
        used.add(spec.name)
    return tuple(specs)
