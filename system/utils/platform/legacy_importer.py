#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""回灌执行器：按实体把旧库数据写进新库（供 ``manage.py legacy_import`` 调用）。

单独成模块是为了让命令文件只留参数与输出编排，同时便于单测直接驱动某个实体的导入。
所有关联都按自然键解析（``code`` / ``username`` / ``name``，模型字段树用「名称 | 类型」复合键），
因此不依赖旧库主键；引用缺失只计数告警，单实体失败只跳过该实体。
"""

from pathlib import Path
from typing import Any

from django.apps import apps
from django.core.files.base import ContentFile

from system.utils.platform import legacy_transfer as lt

# 依赖顺序：被引用者先导入（部门 → 角色/数据权限/配置 → 用户 → 订阅/字段权限 → 附件 → 公告）
ORDER = (
    "depts",
    "roles",
    "data_permissions",
    "system_configs",
    "settings",
    "users",
    "personal_configs",
    "user_msg_subscriptions",
    "system_msg_subscriptions",
    "field_permissions",
    "files",
    "notices",
)


class Importer:
    """按实体搬运数据（自然键 upsert，引用缺失计数跳过）。"""

    def __init__(self, source: Path, dry_run: bool = False) -> None:
        self.source = source
        self.files_dir = source / lt.FILES_DIR
        self.dry_run = dry_run
        self.skipped_refs: dict[str, int] = {}
        self.errors: list[str] = []
        self.encrypted_settings: list[str] = []
        self._ref_index_cache: dict = {}
        self.touched_tables: set[str] = set()
        self._depts: list[dict[str, Any]] = []

    # ---------- 实体分派 ----------

    def import_entity(self, name: str, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        if name == "depts":
            return self._import_depts(rows)
        handler = getattr(self, f"_import_{name}", None)
        if handler is None:
            raise ValueError(f"实体 {name} 尚无导入实现")
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
            _, is_created = self._upsert("depts", row, {"code": row["code"]}, defaults)
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
        self._model("roles")
        created = updated = 0
        for row in rows:
            instance = None
            if not self.dry_run:
                instance, is_created = self._upsert(
                    "roles", row, {"code": row["code"]}, self._scalar_defaults("roles", row)
                )
                created += int(is_created)
                updated += int(not is_created)
                self._apply_m2m("roles", row, instance)
        return created, updated, 0

    def _import_data_permissions(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        self._model("data_permissions")
        created = updated = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            instance, is_created = self._upsert(
                "data_permissions", row, {"name": row["name"]}, self._scalar_defaults("data_permissions", row)
            )
            created += int(is_created)
            updated += int(not is_created)
            self._apply_m2m("data_permissions", row, instance)
        return created, updated, 0

    def _import_system_configs(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        self._model("system_configs")
        created = updated = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            _, is_created = self._upsert(
                "system_configs", row, {"key": row["key"]}, self._scalar_defaults("system_configs", row)
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
            instance, is_created = self._upsert("users", row, {"username": row["username"]}, defaults)
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
        self._model("personal_configs")
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
            _, is_created = self._upsert("personal_configs", row, {"owner": owner, "key": row["key"]}, defaults)
            created += int(is_created)
            updated += int(not is_created)
        return created, updated, skipped

    def _import_settings(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        """系统设置（4.x 的主配置表）：加密项的取值跨库不可解，只搬元信息并列告警。"""
        self._model("settings")
        created = updated = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            defaults = self._scalar_defaults("settings", row)
            if row.get("encrypted"):
                defaults.pop("value", None)
                self.encrypted_settings.append(str(row.get("name") or ""))
            _, is_created = self._upsert("settings", row, {"name": row["name"]}, defaults)
            created += int(is_created)
            updated += int(not is_created)
        return created, updated, 0

    def _import_field_permissions(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        self._model("field_permissions")
        created = updated = skipped = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            role = self._resolve("roles", "code", row.get("role") or "")
            menu = self._resolve("menus", "name", row.get("menu") or "")
            if role is None or menu is None:
                skipped += 1
                self._skip(f"field_permissions={row.get('signature')}")
                continue
            instance, is_created = self._upsert("field_permissions", row, {"role": role, "menu": menu}, {})
            created += int(is_created)
            updated += int(not is_created)
            self._apply_m2m("field_permissions", row, instance)
        return created, updated, skipped

    def _import_user_msg_subscriptions(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        self._model("user_msg_subscriptions")
        created = updated = skipped = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            user = self._resolve("users", "username", row.get("user") or "")
            if user is None:
                skipped += 1
                self._skip(f"user_msg_subscriptions={row.get('signature')}")
                continue
            defaults = self._scalar_defaults("user_msg_subscriptions", row)
            defaults.pop("user", None)
            _, is_created = self._upsert(
                "user_msg_subscriptions", row, {"user": user, "message_type": row["message_type"]}, defaults
            )
            created += int(is_created)
            updated += int(not is_created)
        return created, updated, skipped

    def _import_system_msg_subscriptions(self, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        self._model("system_msg_subscriptions")
        created = updated = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            instance, is_created = self._upsert(
                "system_msg_subscriptions",
                row,
                {"message_type": row["message_type"]},
                self._scalar_defaults("system_msg_subscriptions", row),
            )
            created += int(is_created)
            updated += int(not is_created)
            self._apply_m2m("system_msg_subscriptions", row, instance)
        return created, updated, 0

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
        self._model("notices")
        created = updated = 0
        for row in rows:
            if self.dry_run:
                created += 1
                continue
            instance, is_created = self._upsert(
                "notices", row, {"title": row["title"]}, self._scalar_defaults("notices", row)
            )
            created += int(is_created)
            updated += int(not is_created)
            self._apply_m2m("notices", row, instance)
            self._apply_reads(row, instance)
        return created, updated, 0

    def _upsert(self, name: str, row: dict[str, Any], lookup: dict[str, Any], defaults: dict[str, Any]):
        """按原主键优先落库。

        新库是干净的，因此可以沿用旧库的 ID（``UserInfo`` 为自增主键、其余多为 UUID）——
        二开 app 表里指向用户 / 部门 / 角色的外键值因此仍然有效，外部系统保存的 ID 也不会失效。
        命中已有记录（主键或自然键）时更新，不覆盖 ID。
        """
        model = self._model(name)
        pk = str(row.get("id") or "")
        instance = model._base_manager.filter(pk=pk).first() if pk else None
        if instance is None and lookup:
            instance = model._base_manager.filter(**lookup).first()
        if instance is None:
            # 查询条件同时是创建字段（与原 update_or_create 语义一致：自然键要落进新记录）
            instance = model(**{**defaults, **lookup})
            if pk:
                instance.pk = pk
            instance.save(force_insert=True)
            self.touched_tables.add(model._meta.db_table)
            return instance, True
        for key, value in defaults.items():
            setattr(instance, key, value)
        instance.save()
        self.touched_tables.add(model._meta.db_table)
        return instance, False

    def reset_sequences(self) -> int:
        """把按原值写入的表序列推到最大值，避免后续新建记录撞主键。"""
        from django.db import connection

        reset = 0
        with connection.cursor() as cursor:
            for table in sorted(self.touched_tables):
                cursor.execute("select pg_get_serial_sequence(%s, 'id')", [table])
                found = cursor.fetchone()
                if not found or not found[0]:
                    continue
                cursor.execute(f"select setval(%s, coalesce((select max(id) from {table}), 1))", [found[0]])
                reset += 1
        return reset

    def import_extra_tables(self, tables: dict[str, list[dict[str, Any]]]) -> list[tuple[str, int, int]]:
        """二开 app 自己的表：整表原样搬运（表结构由用户 app 的迁移建立，按同 ID 写回）。"""
        by_table = {model._meta.db_table: model for model in apps.get_models()}
        results: list[tuple[str, int, int]] = []
        for table, rows in tables.items():
            model = by_table.get(table)
            if model is None:
                self._skip(f"extra_table={table}")
                results.append((table, 0, len(rows)))
                continue
            fields = {field.name: field for field in model._meta.concrete_fields}
            fields.update({field.attname: field for field in model._meta.concrete_fields})
            created = skipped = 0
            for row in rows:
                payload = {column: lt.deserialize_value(value) for column, value in row.items() if column in fields}
                pk = payload.get(model._meta.pk.attname)
                if pk and model._base_manager.filter(pk=pk).exists():
                    skipped += 1
                    continue
                model._base_manager.create(**payload)
                created += 1
            self.touched_tables.add(table)
            results.append((table, created, skipped))
        return results

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
        """按自然键解析多对多；自然键可以是单字段或复合键（如模型字段树 名称+类型）。"""
        if instance is None:
            return
        for field, (target, target_key) in lt.ENTITIES[name].get("m2m", {}).items():
            values = [item for item in (row.get(field) or []) if item]
            if not values:
                continue
            index = self._ref_index(target, lt.normalize_keys(target_key))
            found = [index[value] for value in values if value in index]
            for missing in sorted(set(values) - set(index)):
                self._skip(f"{name}.{field}={missing}")
            getattr(instance, field).set(found)

    def _ref_index(self, entity: str, keys: tuple[str, ...]) -> dict[str, Any]:
        """引用实体索引：{自然键（复合键用 | 连接）: 实例}。"""
        cache_key = (entity, keys)
        if cache_key not in self._ref_index_cache:
            index = {}
            for obj in self._ref_manager(entity).all():
                index[lt.join_key([getattr(obj, key, "") for key in keys])] = obj
            self._ref_index_cache[cache_key] = index
        return self._ref_index_cache[cache_key]

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
