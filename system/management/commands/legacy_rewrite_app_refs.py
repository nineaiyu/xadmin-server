#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""改写二开 app 的迁移引用（让它在 5.x 上重新可用；默认 dry-run）。

四域切分后，二开 app 迁移里写死的旧引用会让 ``migrate`` 直接失败（节点不存在 / 模型解析不到），
这是「清库重建 + 回灌」对二开不友好的机制性根源。本命令按**历史 tag 推导的标签映射**改写：

- 外键目标：``to="system.UserInfo"`` → ``to="identity.UserInfo"``；
- 依赖节点：指向已消失节点的元组 → 该 app 当前的初始迁移，并补上目标 app 的依赖。

用法::

    python manage.py legacy_rewrite_app_refs --app-path ../my_app --tag v4.2.5          # 看 diff
    python manage.py legacy_rewrite_app_refs --app-path ../my_app --tag v4.2.5 --apply

退出码：``0`` = 无未解决项（可能改写了若干处或无需改写）；``1`` = 有未解决项（需人工）；
``2`` = 输入/文件错误。改写结果请用 ``manage.py makemigrations --check`` 复核。
"""

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from ops.legacy import app_refs as legacy_app_refs


class Command(BaseCommand):
    help = "Rewrite legacy app_label references in a third-party app's migrations"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--app-path", required=True, help="二开 app 目录（含 migrations/）")
        parser.add_argument("--tag", required=True, help="旧版本 tag（如 v4.2.5）：标签映射的来源")
        parser.add_argument("--apply", action="store_true", help="写回文件（默认只打印 diff）")
        parser.add_argument("--json", action="store_true", help="输出 JSON（供脚本消费）")

    def handle(self, *args: Any, **options: Any) -> None:
        app_path = Path(options["app_path"]).expanduser().resolve()
        if not (app_path / "migrations").is_dir():
            raise CommandError(f"未找到 migrations 目录：{app_path / 'migrations'}")
        try:
            moves = legacy_app_refs.label_moves_from_tag(options["tag"])
        except Exception as exc:  # noqa: BLE001 - tag 不可用（单仓检出等）给出明确报错
            raise CommandError(f"无法从 tag {options['tag']} 推导标签映射：{exc}") from exc
        if not moves:
            raise CommandError(f"tag {options['tag']} 未推导出任何标签迁移（仓库缺少该 tag 的历史？）")

        result = legacy_app_refs.rewrite_app(app_path, moves, dry_run=not options["apply"])
        result["moves"] = moves
        if options["json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            self._render(result, app_path)
        if result["unresolved"]:
            raise SystemExit(1)

    def _render(self, result: dict[str, Any], app_path: Path) -> None:
        mode = "已改写" if not result["dry_run"] else "计划（dry-run）"
        self.stdout.write(f"迁移引用改写{mode}：{app_path}")
        self.stdout.write(f"  · 标签映射 {len(result['moves'])} 条（由 tag 推导）")
        for source, target in sorted(result["moves"].items()):
            self.stdout.write(f"      {source} → {target}")
        if not result["files"]:
            self.stdout.write("  · 无需改写的引用")
        for item in result["files"]:
            self.stdout.write(f"  · {item['file']}")
            for line in item["fk_targets"]:
                self.stdout.write(f"      外键目标：{line}")
            for line in item["dependencies"]:
                self.stdout.write(f"      依赖：{line}")
        if result["diff"]:
            self.stdout.write("\n--- diff ---")
            for line in result["diff"]:
                self.stdout.write(line)
        if result["unresolved"]:
            self.stdout.write(f"\n未解决项 {len(result['unresolved'])} 条（未改写，需人工）：")
            for item in result["unresolved"]:
                self.stdout.write(f"  ! {item}")
        else:
            self.stdout.write("\n未解决项 0；改完请跑 manage.py makemigrations --check 复核。")
