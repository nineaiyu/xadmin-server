#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""把 ``legacy_export`` 的产物回灌进新库（四域切分之后的结构）。

前置：新库已完成初始化（``migrate`` + ``ops/init_data.py`` 或 ``load_init_json``），
菜单与权限点由种子提供，本命令只搬运业务数据（组织、角色、用户、数据权限、系统配置、
附件、公告），关联按自然键解析（``code`` / ``username`` / ``name``），旧库的 UUID 不保留。

幂等：按自然键 upsert（重复执行只更新不重复建）；引用缺失（如角色绑定的菜单在当前种子中
不存在）会计数跳过并汇总告警，不中断导入。

用法::

    python manage.py legacy_import --input /tmp/xadmin-legacy
    python manage.py legacy_import --input /tmp/xadmin-legacy --dry-run
"""

import json
from pathlib import Path
from typing import Any

from django.apps import apps
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from system.utils.platform import legacy_transfer as lt

# 依赖顺序：被引用者先导入（部门 → 角色/数据权限 → 用户 → 附件 → 公告）
ORDER = ("depts", "roles", "data_permissions", "system_configs", "users", "personal_configs", "files", "notices")

# 记录类型/载荷的字段名（与 legacy_transfer 的模型定义对应）
SIGNATURE_SPECS = {
    "files": (("filename", "filesize"), "filepath"),
    "notices": (("title",), None),
}


class Command(BaseCommand):
    help = "Import legacy business data (exported by legacy_export) into the current database"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--input", required=True, help="legacy_export 的输出目录")
        parser.add_argument("--include", default=None, help="只导入指定实体，逗号分隔")
        parser.add_argument("--dry-run", action="store_true", help="只校验与统计，不写库")

    def handle(self, *args: Any, **options: Any) -> None:
        source = Path(options["input"]).expanduser()
        manifest = source / "export.json"
        if not manifest.exists():
            raise CommandError(f"未找到导出文件：{manifest}")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        lt.validate_payload(payload)
        try:
            names = lt.parse_include(options["include"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        importer = Importer(source, dry_run=options["dry_run"])
        self.stdout.write(f"导出包：{manifest}（源版本 {payload.get('source_version')}）")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("dry-run：只统计，不写库"))
        for name in [item for item in ORDER if item in names]:
            rows = payload["data"].get(name) or []
            # 单实体失败不中断整体：否则后续实体与收尾（如部门父子链接）都不会执行
            try:
                created, updated, skipped = importer.import_entity(name, rows)
            except Exception as exc:  # noqa: BLE001 - 逐实体隔离，最终统一汇总
                importer.record_error(f"{name}: {exc}")
                self.stderr.write(self.style.ERROR(f"  · {name}: 导入失败（已跳过该实体）：{exc}"))
                continue
            self.stdout.write(f"  · {name}: 新建 {created}，更新 {updated}，跳过 {skipped}")

        importer.finish()
        for message in importer.errors:
            self.stderr.write(self.style.ERROR(f"  ! {message}"))
        if importer.skipped_refs:
            self.stdout.write(self.style.WARNING(f"引用缺失 {sum(importer.skipped_refs.values())} 处："))
            for ref, count in sorted(importer.skipped_refs.items()):
                self.stdout.write(f"    - {ref}: {count}")
            self.stdout.write("  多为菜单/权限点未灌种子或数据本身已被删除；确认后可忽略。")
        self.stdout.write(self.style.SUCCESS("\n回灌完成；请执行 upgrade_check 复核，并重启后端容器。"))
        if importer.skipped_refs and not options["dry_run"]:
            self.stdout.write(self.style.WARNING("存在引用缺失，建议逐项核对上面的清单。"))


class Importer:
    """按实体搬运数据（自然键 upsert，引用缺失计数跳过）。"""

    def __init__(self, source: Path, dry_run: bool = False) -> None:
        self.source = source
        self.files_dir = source / lt.FILES_DIR
        self.dry_run = dry_run
        self.skipped_refs: dict[str, int] = {}
        self.errors: list[str] = []
        self._depts: list[dict[str, Any]] = []

    # ---------- 实体分派 ----------

    def import_entity(self, name: str, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        if name == "depts":
            return self._import_depts(rows)
        handler = getattr(self, f"_import_{name}", None)
        if handler is None:
            raise CommandError(f"实体 {name} 尚无导入实现")
        return handler(rows)

    def finish(self) -> None:
        """部门父子关系在全部部门落库后统一解析（避免自引用顺序问题）。"""
        self._link_dept_parents()

    # ---------- 部门 ----------

    def _import_depts(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        model = self._model("depts")
        created = updated = 0
        for row in rows:
            defaults = self._scalar_defaults("depts", row)
            if self.dry_run:
                created += 1
                continue
            _, is_created = model._base_manager.update_or_create(code=row["code"], defaults=defaults)
            created += int(is_created)
            updated += int(not is_created)
            self._depts.append(row)
        if not self.dry_run:
            for row in rows:
                self._apply_m2m("depts", row, model._base_manager.filter(code=row["code"]).first())
        return created, updated, 0

    def _link_dept_parents(self) -> None:
        if self.dry_run:
            return
        model = self._model("depts")
        for row in self._depts:
            parent_code = row.get("parent") or ""
            parent = model._base_manager.filter(code=parent_code).first() if parent_code else None
            if parent_code and parent is None:
                self._skip(f"depts.parent={parent_code}")
            instance = model._base_manager.filter(code=row["code"]).first()
            if instance and instance.parent_id != (parent.pk if parent else None):
                instance.parent = parent
                instance.save(update_fields=["parent"])

    # ---------- 其余实体 ----------

    def _import_roles(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        model = self._model("roles")
        created = updated = 0
        for row in rows:
            instance = None
            if not self.dry_run:
                instance, is_created = model._base_manager.update_or_create(
                    code=row["code"], defaults=self._scalar_defaults("roles", row)
                )
                created += int(is_created)
                updated += int(not is_created)
                self._apply_m2m("roles", row, instance)
        return created, updated, 0

    def _import_data_permissions(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        model = self._model("data_permissions")
        created = updated = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            instance, is_created = model._base_manager.update_or_create(
                name=row["name"], defaults=self._scalar_defaults("data_permissions", row)
            )
            created += int(is_created)
            updated += int(not is_created)
            self._apply_m2m("data_permissions", row, instance)
        return created, updated, 0

    def _import_system_configs(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        model = self._model("system_configs")
        created = updated = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            _, is_created = model._base_manager.update_or_create(
                key=row["key"], defaults=self._scalar_defaults("system_configs", row)
            )
            created += int(is_created)
            updated += int(not is_created)
        return created, updated, 0

    def _import_users(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        model = self._model("users")
        created = updated = skipped = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            defaults = self._scalar_defaults("users", row)
            dept_code = row.get("dept") or ""
            if dept_code:
                dept = self._resolve("depts", "code", dept_code)
                if dept is None:
                    self._skip(f"users.dept={dept_code}")
                    skipped += 1
                else:
                    defaults["dept"] = dept
            instance, is_created = model._base_manager.update_or_create(username=row["username"], defaults=defaults)
            created += int(is_created)
            updated += int(not is_created)
            self._restore_user_files(row, instance, model)
            self._apply_m2m("users", row, instance)
        return created, updated, skipped

    def _restore_user_files(self, row: dict[str, Any], instance: Any, model: Any) -> None:
        """用户头像等文件字段：有归档文件才写入（缺失只计数，不影响用户记录）。"""
        for field in lt.USER_FILE_FIELDS:
            path = row.get(f"{field}_path") or ""
            if not path:
                continue
            current = getattr(instance, field, None)
            if current and getattr(current, "name", "") == path:
                continue
            content = self._read_archived_file(path)
            if content is None:
                self._skip(f"users.{field}={path}")
                continue
            getattr(instance, field).save(path, ContentFile(content), save=False)
            instance.save(update_fields=[field])

    def _import_personal_configs(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        model = self._model("personal_configs")
        created = updated = skipped = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            owner = self._resolve("users", "username", row.get("owner") or "")
            if owner is None:
                skipped += 1
                self._skip(f"personal_configs.owner={row.get('owner')}")
                continue
            defaults = self._scalar_defaults("personal_configs", row)
            defaults.pop("owner", None)
            _, is_created = model._base_manager.update_or_create(owner=owner, key=row["key"], defaults=defaults)
            created += int(is_created)
            updated += int(not is_created)
        return created, updated, skipped

    def _import_files(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        """附件：物理文件必须**写入**存储后再落库。

        ``UploadFile.save()`` 会读 ``filepath`` 流算 md5sum，因此不能只把存储名当字符串塞进
        ``filepath``（缺文件时保存会因打开失败而中断整批导入）；物理文件缺失的记录直接跳过
        （留下记录但下载不可用没有意义），已有记录只更新元数据、不重写文件。
        """
        model = self._model("files")
        created = updated = skipped = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            lookup = {"filename": row["filename"], "filesize": row.get("filesize") or 0}
            defaults = self._scalar_defaults("files", row)
            defaults.pop("filepath", None)
            existing = model._base_manager.filter(**lookup).first()
            if existing is not None:
                for key, value in defaults.items():
                    setattr(existing, key, value)
                existing.save()
                updated += 1
                continue
            storage_name = row.get("storage_name") or ""
            content = self._read_archived_file(storage_name)
            if content is None:
                skipped += 1
                self._skip(f"files.missing={storage_name or row['filename']}")
                continue
            instance = model(**defaults)
            instance.filepath.save(storage_name or row["filename"], ContentFile(content), save=False)
            instance.save()
            created += 1
        return created, updated, skipped

    def _import_notices(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        model = self._model("notices")
        created = updated = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            instance, is_created = model._base_manager.update_or_create(
                title=row["title"], defaults=self._scalar_defaults("notices", row)
            )
            created += int(is_created)
            updated += int(not is_created)
            self._apply_m2m("notices", row, instance)
            self._apply_reads(row, instance)
        return created, updated, 0

    # ---------- 通用助手 ----------

    def _model(self, name: str) -> Any:
        spec = lt.ENTITIES.get(name) or lt.REFERENCE_ENTITIES[name]
        return apps.get_model(*spec["import_model"])

    def _scalar_defaults(self, name: str, row: dict[str, Any]) -> dict[str, Any]:
        spec = lt.ENTITIES[name]
        defaults = {field: lt.deserialize_value(row.get(field)) for field in spec["fields"] if field in row}
        # 软删模型：同名记录曾进回收站时一并恢复
        if any(field.name == "deleted_at" for field in self._model(name)._meta.fields):
            defaults["deleted_at"] = None
        # 自然键字段不放在 defaults（update_or_create 的查询条件已含）
        key = spec["key"]
        if key in defaults and key in spec["fields"] and key != "signature":
            defaults.pop(key)
        return defaults

    def _resolve(self, entity: str, key: str, value: Any) -> Any:
        if not value:
            return None
        return self._ref_manager(entity).filter(**{key: value}).first()

    def _ref_manager(self, name: str) -> Any:
        """引用解析走默认管理器：回收站（软删）中的数据不参与关联。

        upsert 目标用 ``_base_manager``（同名软删记录要能恢复），但关联不能指向回收站里的
        记录——否则会得到一个「角色绑着已删除菜单」的库。
        """
        return self._model(name).objects

    def _apply_m2m(self, name: str, row: dict[str, Any], instance: Any) -> None:
        if instance is None:
            return
        for field, (target, target_key) in lt.ENTITIES[name].get("m2m", {}).items():
            values = [item for item in (row.get(field) or []) if item]
            if not values:
                continue
            instances = list(self._ref_manager(target).filter(**{f"{target_key}__in": values}))
            found = {str(getattr(item, target_key, "")) for item in instances}
            for missing in sorted(set(values) - found):
                self._skip(f"{name}.{field}={missing}")
            getattr(instance, field).set(instances)

    def _apply_reads(self, row: dict[str, Any], instance: Any) -> None:
        reads = row.get("reads") or []
        if not reads:
            return
        model = self._model("notices")
        reads_model = apps.get_model("notifications", "MessageUserRead")
        notice_field = next(
            field.name for field in reads_model._meta.fields if getattr(field, "related_model", None) is model
        )
        for item in reads:
            owner = self._resolve("users", "username", item.get("owner"))
            if owner is None:
                self._skip(f"notices.reads={item.get('owner')}")
                continue
            reads_model.objects.update_or_create(
                **{notice_field: instance, "owner": owner},
                defaults={"unread": bool(item.get("unread", True))},
            )

    def _read_archived_file(self, storage_name: str) -> bytes | None:
        """读取导出目录里的附件内容；文件缺失返回 None（调用方跳过该记录）。"""
        if not storage_name:
            return None
        local = self.files_dir / storage_name
        if not local.exists():
            return None
        return local.read_bytes()

    def record_error(self, message: str) -> None:
        self.errors.append(message)

    def _skip(self, ref: str) -> None:
        self.skipped_refs[ref] = self.skipped_refs.get(ref, 0) + 1
