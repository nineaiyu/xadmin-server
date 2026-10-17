#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""装载计划的编排：应用选择 → 依赖闭包 → 拓扑排序 → 层内顺序。

计划是纯计算（只读文件系统元数据，不连数据库），同样的输入永远得到同样的顺序——
这是「按 app 导出」与「按 app 导入」能够对齐的前提。
"""

from collections.abc import Sequence

from .discover import Discovery
from .types import (
    KIND_SAMPLE,
    PLATFORM,
    SeedFileSpec,
    SeedManifest,
    SeedPlan,
    SeedPlanError,
)


def build_plan(
    discovery: Discovery,
    *,
    apps: Sequence[str] | None = None,
    include_sample: bool = False,
) -> SeedPlan:
    """按发现结果生成装载计划。

    :param apps: 只装载这些 app（自动补上 ``depends`` 闭包）；``None`` 表示全部已发现 app
    :param include_sample: 是否包含 ``sample`` 类种子（示例数据）
    """

    requested = list(dict.fromkeys(apps)) if apps is not None else list(discovery.manifests)
    selected = _expand(requested, discovery.manifests)
    ordered = _topological_order(selected, discovery.manifests)

    files: dict[str, tuple[SeedFileSpec, ...]] = {}
    samples: list[SeedFileSpec] = _sort([spec for spec in discovery.platform.files if spec.kind == KIND_SAMPLE])
    grants: list[SeedFileSpec] = []
    if discovery.platform.grants is not None:
        grants.append(discovery.platform.grants)
    for app in ordered:
        manifest = discovery.manifests[app]
        contract, sample = _split(manifest.files)
        files[app] = tuple(_sort(contract))
        samples.extend(_sort([*sample, *manifest.sample]))
        if manifest.grants is not None:
            grants.append(manifest.grants)

    notes = _build_notes(requested, selected)
    return SeedPlan(
        # 平台层顺序由发现阶段按历史模型序组装，不能再按 order 重排
        # （同一 order 的文件会退化成文件名字典序，改变既有装载顺序）
        platform=tuple(spec for spec in discovery.platform.files if spec.kind != KIND_SAMPLE),
        apps=tuple(ordered),
        files=files,
        samples=tuple(samples) if include_sample else (),
        grants=tuple(grants),
        notes=tuple(notes),
    )


def _split(specs: Sequence[SeedFileSpec]) -> tuple[list[SeedFileSpec], list[SeedFileSpec]]:
    contract = [spec for spec in specs if spec.kind != KIND_SAMPLE]
    sample = [spec for spec in specs if spec.kind == KIND_SAMPLE]
    return contract, sample


def _sort(specs: Sequence[SeedFileSpec]) -> list[SeedFileSpec]:
    """层内顺序：``order`` 优先，同序按文件名稳定排序。"""

    return sorted(specs, key=lambda spec: (spec.order, spec.name))


def _expand(requested: Sequence[str], manifests: dict[str, SeedManifest]) -> list[str]:
    """补全依赖闭包（保持首次出现顺序，最终顺序由拓扑排序决定）。"""

    selected: list[str] = []
    pending = list(requested)
    while pending:
        app = pending.pop(0)
        if app in selected:
            continue
        if app not in manifests:
            raise SeedPlanError(f"未知应用 {app!r}：它没有 {app}/seeds/manifest.json（可用 --list 查看已发现的应用）")
        selected.append(app)
        for dep in manifests[app].depends:
            if dep == PLATFORM:
                continue
            if dep not in manifests:
                raise SeedPlanError(f"应用 {app!r} 依赖 {dep!r}，但该应用未提供 seeds/manifest.json")
            pending.append(dep)
    return selected


def _topological_order(selected: Sequence[str], manifests: dict[str, SeedManifest]) -> list[str]:
    """按 ``depends`` 做深度优先拓扑排序；成环直接报错（不允许「碰运气」的顺序）。"""

    result: list[str] = []
    visiting: list[str] = []
    done: set[str] = set()

    def visit(app: str) -> None:
        if app in done:
            return
        if app in visiting:
            cycle = " → ".join([*visiting[visiting.index(app) :], app])
            raise SeedPlanError(f"应用种子依赖成环：{cycle}")
        visiting.append(app)
        for dep in manifests[app].depends:
            if dep != PLATFORM:
                visit(dep)
        visiting.pop()
        done.add(app)
        result.append(app)

    for app in selected:
        visit(app)
    return result


def _build_notes(requested: Sequence[str], selected: Sequence[str]) -> list[str]:
    extra = [app for app in selected if app not in requested]
    if not extra:
        return []
    return [f"按依赖补充装载：{'、'.join(extra)}"]
