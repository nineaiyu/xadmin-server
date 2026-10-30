#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导出稳定的 OpenAPI schema（前端类型生成的契约真源）。

用法::

    python manage.py dump_openapi                 # 写入 docs/schema/openapi.json
    python manage.py dump_openapi --check         # 只校验文件与当前代码一致（CI）

稳定性：运行期默认值（uuid4 主键、当前时间）在导出时剔除（见
``devtools.services.openapi``），两次生成结果逐字节一致，便于 diff 与漂移门禁。
"""

import json
from pathlib import Path
from typing import Any, cast

from django.core.management.base import BaseCommand, CommandError

from devtools.services.openapi import normalize_schema
from server.const import PROJECT_DIR


class Command(BaseCommand):
    help = "Export a stable OpenAPI schema (docs/schema/openapi.json) for client type generation"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--file",
            default=str(Path(PROJECT_DIR) / "docs" / "schema" / "openapi.json"),
            help="输出路径（默认 docs/schema/openapi.json）",
        )
        parser.add_argument("--check", action="store_true", help="只校验现有文件是否与代码一致（不写盘）")

    def handle(self, *args: Any, **options: Any) -> None:
        from drf_spectacular.generators import SchemaGenerator

        generator = cast(Any, SchemaGenerator)()
        schema = normalize_schema(generator.get_schema(request=None, public=True))
        text = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path = Path(options["file"])
        if options["check"]:
            if not path.exists():
                raise CommandError(f"OpenAPI schema 不存在：{path}；先运行 python manage.py dump_openapi")
            if path.read_text(encoding="utf-8") != text:
                raise CommandError(f"OpenAPI schema 已漂移：{path}；重新运行 python manage.py dump_openapi")
            self.stdout.write(self.style.SUCCESS(f"OpenAPI schema 一致：{path}"))
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"OpenAPI schema 已写入：{path}"))
