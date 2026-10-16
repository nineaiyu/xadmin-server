#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""导出四域切分前旧库的核心业务数据（配合 ``legacy_import`` 在新库回灌）。

适用场景：库结构停留在四域切分之前（``system_userinfo`` 等旧表名），无法通过迁移收敛
（``upgrade_check`` 判定 ``legacy-chain-pre-split``）。处置为「备份 → 清库重建 → 回灌」，
本命令负责第一步：把组织、角色、用户、数据权限、系统配置、附件、公告导出为 JSON +
附件目录，关联一律用自然键（``code`` / ``username`` / ``name``），不依赖主键。

用法::

    python manage.py legacy_export --output /tmp/xadmin-legacy
    python manage.py legacy_export --output /tmp/xadmin-legacy --include users,depts

附件物理文件按存储路径原样拷贝到 ``<output>/files/``；登录/操作日志与临时数据不导出
（体量大且无业务价值，审计留档请用数据库备份）。
"""

import json
import shutil
from pathlib import Path
from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

from server.const import VERSION
from system.utils.platform import legacy_transfer as lt

DEFAULT_INCLUDE = ",".join(lt.entity_names())


class Command(BaseCommand):
    help = "Export legacy (pre-domain-split) business data for re-import on the new schema"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--output", required=True, help="输出目录（写入 export.json 与 files/）")
        parser.add_argument(
            "--include",
            default=None,
            help=f"要导出的实体，逗号分隔（默认全部：{DEFAULT_INCLUDE}）",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        output = Path(options["output"]).expanduser()
        try:
            names = lt.parse_include(options["include"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        files_dir = output / lt.FILES_DIR
        output.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        for name in names:
            rows = self._export_entity(name, files_dir)
            data[name] = rows
            self.stdout.write(f"  · {name}: {len(rows)} 行")

        payload = lt.build_payload(VERSION, data)
        target = output / "export.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"\n导出完成：{target}"))
        self.stdout.write(f"  实体：{', '.join(names)}")
        self.stdout.write(f"  附件目录：{files_dir}（在目标机用同一目录执行 legacy_import）")

    def _export_entity(self, name: str, files_dir: Path) -> list[dict[str, Any]]:
        spec = lt.ENTITIES[name]
        model = apps.get_model(*spec["export_model"])
        rows: list[dict[str, Any]] = []
        for obj in model._default_manager.all():
            row = {field: lt.serialize_value(getattr(obj, field, None)) for field in spec["fields"]}
            self._fill_fk(obj, spec, row)
            self._fill_m2m(obj, spec, row)
            if "storage_field" in spec:
                self._dump_file(obj, spec, row, files_dir)
            for field in spec.get("file_fields", ()):
                self._dump_model_file(obj, field, row, files_dir)
            if "reads" in spec:
                row["reads"] = self._collect_reads(obj, spec)
            row[spec["key"]] = self._natural_key(obj, spec, row)
            rows.append(row)
        return rows

    def _fill_fk(self, obj: Any, spec: dict[str, Any], row: dict[str, Any]) -> None:
        for field, (_, target_key) in spec.get("fk", {}).items():
            related = getattr(obj, field, None)
            row[field] = str(getattr(related, target_key, "")) if related else ""

    def _fill_m2m(self, obj: Any, spec: dict[str, Any], row: dict[str, Any]) -> None:
        """多对多按自然键导出；自然键可以是单字段或复合键（如模型字段树 名称+类型）。"""
        for field, (_, target_key) in spec.get("m2m", {}).items():
            manager = getattr(obj, field, None)
            if manager is None:
                row[field] = []
                continue
            keys = lt.normalize_keys(target_key)
            row[field] = sorted(
                value
                for value in (lt.join_key([getattr(item, key, "") for key in keys]) for item in manager.all())
                if value
            )

    def _dump_file(self, obj: Any, spec: dict[str, Any], row: dict[str, Any], files_dir: Path) -> None:
        field_file = getattr(obj, spec["storage_field"], None)
        storage_name = getattr(field_file, "name", "") or ""
        row["storage_name"] = storage_name
        if not storage_name:
            return
        target = files_dir / storage_name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with field_file.storage.open(storage_name, "rb") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        except OSError as exc:  # 物理文件缺失时保留元数据，导入侧会跳过
            self.stderr.write(f"    警告：附件 {storage_name} 无法读取（{exc}），仅导出记录")

    def _dump_model_file(self, obj: Any, field: str, row: dict[str, Any], files_dir: Path) -> None:
        """导出模型自带的文件字段（用户头像等）：记录存储路径并拷贝物理文件。"""
        field_file = getattr(obj, field, None)
        storage_name = getattr(field_file, "name", "") or ""
        row[f"{field}_path"] = storage_name
        if not storage_name:
            return
        target = files_dir / storage_name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with field_file.storage.open(storage_name, "rb") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        except OSError as exc:
            self.stderr.write(f"    警告：{field} {storage_name} 无法读取（{exc}），仅导出记录")

    def _collect_reads(self, obj: Any, spec: dict[str, Any]) -> list[dict[str, Any]]:
        reads_spec = spec["reads"]
        model = apps.get_model(*reads_spec["model"])
        field_name = next(
            field.name for field in model._meta.fields if getattr(field, "related_model", None) is type(obj)
        )
        rows = []
        for record in model._default_manager.filter(**{field_name: obj}).select_related("owner"):
            rows.append(
                {
                    "owner": str(getattr(record.owner, reads_spec["owner"], "") or ""),
                    "unread": bool(getattr(record, reads_spec["flag"], True)),
                }
            )
        return rows

    def _natural_key(self, obj: Any, spec: dict[str, Any], row: dict[str, Any]) -> str:
        if spec["key"] == "signature":
            if "filesize" in row:
                return lt.file_signature(row.get("filename", ""), row.get("filesize"))
            return lt.notice_signature(row.get("title", ""), row.get("created_time"))
        return str(getattr(obj, spec["key"], "") or "")
