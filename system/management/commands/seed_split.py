#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : seed_split
"""按应用归属拆分内置种子文件（纯文件操作，不连数据库）。

与 ``dump_init_json``（从库导出全表）的分工：本命令只做「现有种子行的归属重排」，
输入输出都是仓库里的 JSON 文件，因此可在任何环境跑、可 review、可回滚。

安全约束：

- **迁移前后行集合必须严格相等**（pk + 全字段）：不一致时拒绝写盘（fail-closed），
  避免拆分过程丢行或把运行期数据带进种子；
- 幂等：重复执行结果不变（未选中的应用保持原层不动）；
- 只处理可归属的六类种子（菜单 / 菜单元数据 / 字段树 / 字段白名单 / 配置 / 字典）；
  业务示例数据不在本命令范围内。
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from system.services.seeding import (
    PLATFORM,
    build_plan,
    discover,
    row_owners,
)
from system.services.seeding.split import SPLIT_MODELS, write_layer_model

LayerRows = dict[str, list[tuple[str, dict[str, Any]]]]
Bucket = dict[str, dict[str, list[dict[str, Any]]]]


class Command(BaseCommand):
    help = "按应用归属拆分内置种子文件（不连数据库；迁移前后行集合必须相等）"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--apps", default="", help="只迁移这些应用（逗号分隔），默认全部")
        parser.add_argument("--dry-run", action="store_true", help="只打印迁移计划，不写文件")
        parser.add_argument("--verify-only", action="store_true", help="只做等价性校验，不写文件")

    def handle(self, *args: Any, **options: Any) -> None:
        discovery = discover()
        selected = self._select_apps(options.get("apps"), discovery.manifests)
        plan = build_plan(discovery)

        layers = self._read_layers(plan)
        owners = row_owners(
            {model: [row for _source, row in items] for model, items in layers.items()}, discovery.manifests
        )
        buckets = self._bucket(layers, owners, selected)
        self._verify(layers, buckets)

        total = sum(len(items) for items in layers.values())
        self.stdout.write(
            f"迁移应用：{'、'.join(selected) if selected else '（无）'}；涉及模型 {len(SPLIT_MODELS)} 类，种子行 {total}"
        )
        for app in sorted(buckets):
            counts = {model: len(rows) for model, rows in buckets[app].items() if rows}
            if counts:
                label = "平台层" if app == PLATFORM else app
                self.stdout.write(f"  {label}: " + "、".join(f"{model}={count}" for model, count in counts.items()))

        if options.get("dry_run") or options.get("verify_only"):
            self.stdout.write(self.style.SUCCESS("等价性校验通过（未写文件）"))
            return

        self._write(buckets, discovery.manifests)
        self.stdout.write(self.style.SUCCESS("拆分完成，迁移前后行集合一致"))

    @staticmethod
    def _select_apps(raw: Any, manifests: dict[str, Any]) -> list[str]:
        if not raw:
            return sorted(manifests)
        selected = [item.strip() for item in str(raw).split(",") if item.strip()]
        unknown = [app for app in selected if app not in manifests]
        if unknown:
            raise CommandError(f"未知应用（缺少 seeds/manifest.json）：{'、'.join(unknown)}")
        return selected

    @staticmethod
    def _read_layers(plan: Any) -> LayerRows:
        """读取各层种子行并记录来源层：``{模型: [(来源 app, 行)]}``（保持装载顺序）。"""

        layers: LayerRows = defaultdict(list)
        for spec in plan.all_specs():
            if spec.model not in SPLIT_MODELS or not spec.path.is_file():
                continue
            rows = json.loads(spec.path.read_text(encoding="utf-8"))
            layers[spec.model].extend((spec.app, row) for row in rows)
        return dict(layers)

    @staticmethod
    def _bucket(layers: LayerRows, owners: dict[str, dict[str, str]], selected: list[str]) -> Bucket:
        """把行分到目标层：选中应用迁到自身层，未选中的应用**原地不动**。"""

        buckets: Bucket = defaultdict(lambda: defaultdict(list))
        for model, items in layers.items():
            for source, row in items:
                pk = str(row.get("pk") or "")
                owner = owners.get(model, {}).get(pk, PLATFORM)
                target = owner if owner in selected else source
                buckets[target][model].append(row)
        return buckets

    @staticmethod
    def _verify(layers: LayerRows, buckets: Bucket) -> None:
        """迁移前后逐行相等（pk 集合 + 全字段），否则拒绝写盘。"""

        before = {(model, row["pk"]): row for model, items in layers.items() for _source, row in items}
        after = {
            (model, row["pk"]): row for models in buckets.values() for model, rows in models.items() for row in rows
        }
        missing = sorted(set(before) - set(after))
        extra = sorted(set(after) - set(before))
        changed = sorted(key for key in before if key in after and before[key] != after[key])
        if missing or extra or changed:
            raise CommandError(
                "等价性校验失败，已拒绝写盘："
                f"缺少 {len(missing)} 行、新增 {len(extra)} 行、内容变化 {len(changed)} 行"
                f"（示例：{(missing or extra or changed)[:3]}）"
            )

    def _write(self, buckets: Bucket, manifests: dict[str, Any]) -> None:
        """按「层 × 模型」全量重写：某模型的行全部迁走后，原层文件必须写成空数组。

        落盘统一走 :func:`write_layer_model`：规范文件名 + 收敛同模型的旧拆分文件
        （``menu_<model>.json`` 等），避免同层同主键出现两份。
        """

        base = Path(settings.PROJECT_DIR)
        targets = set(buckets) | {PLATFORM}
        for app in sorted(targets):
            for model in SPLIT_MODELS:
                rows = buckets.get(app, {}).get(model, [])
                if app != PLATFORM and not rows:
                    continue
                write_layer_model(base, app, model, rows, manifests=manifests)
