#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""四域切分前旧库的数据导出（独立脚本，不依赖 Django 代码版本）。

为什么是独立脚本：回灌的第一步必须在**旧库**上执行，而旧版本代码里没有新命令；本脚本只依赖
数据库驱动（psycopg / psycopg2）与表名，可放进旧版本容器里直接跑::

    # 旧库环境（容器内或任意能连库的机器）
    python legacy_export.py --dsn "postgresql://server:pwd@postgresql:5432/xadmin" \\
        --output /tmp/xadmin-legacy --media-root /data/xadmin-server/data/upload

    # --include users,depts 只导出部分实体

产物交给新库：``python manage.py legacy_import --input /tmp/xadmin-legacy``。
关联一律写成自然键（``code`` / ``username`` / ``name``），因此不保留旧库主键，也不需要旧库的模型。
"""

import argparse
import base64
import datetime
import decimal
import json
import os
import shutil
import sys
import uuid

FORMAT = "xadmin-legacy-export"
FORMAT_VERSION = 1
FILES_DIR = "files"

# 实体 → (旧库表名, 自然键列)
TABLE_MAP = {
    "depts": ("system_deptinfo", "code"),
    "roles": ("system_userrole", "code"),
    "users": ("system_userinfo", "username"),
    "data_permissions": ("system_datapermission", "name"),
    "system_configs": ("system_systemconfig", "key"),
    "personal_configs": ("system_userpersonalconfig", "key"),
    "files": ("system_uploadfile", "filename"),
    "notices": ("notifications_messagecontent", "title"),
}

# 只做解析、不搬运的引用实体（菜单由新库种子提供）
REFERENCE_TABLES = {"menus": ("system_menu", "name")}

# 多对多中间表：(实体, 字段, 中间表, 本侧外键列, 目标外键列, 目标实体)
# 公告的目标用户与已读状态同源（through=MessageUserRead），由 _reads 统一导出，不在此列
M2M_MAP = (
    ("depts", "roles", "system_deptinfo_roles", "deptinfo_id", "userrole_id", "roles"),
    ("depts", "rules", "system_deptinfo_rules", "deptinfo_id", "datapermission_id", "data_permissions"),
    ("roles", "menu", "system_userrole_menu", "userrole_id", "menu_id", "menus"),
    ("users", "roles", "system_userinfo_roles", "userinfo_id", "userrole_id", "roles"),
    ("users", "rules", "system_userinfo_rules", "userinfo_id", "datapermission_id", "data_permissions"),
    ("data_permissions", "menu", "system_datapermission_menu", "datapermission_id", "menu_id", "menus"),
    (
        "notices",
        "notice_dept",
        "notifications_messagecontent_notice_dept",
        "messagecontent_id",
        "deptinfo_id",
        "depts",
    ),
    (
        "notices",
        "notice_role",
        "notifications_messagecontent_notice_role",
        "messagecontent_id",
        "userrole_id",
        "roles",
    ),
    ("notices", "file", "notifications_messagecontent_file", "messagecontent_id", "uploadfile_id", "files"),
)

# 实体 → 直接搬运的标量列（新库导入侧认这些字段名）
SCALAR_COLUMNS = {
    "depts": ("name", "code", "rank", "is_active", "auto_bind"),
    "roles": ("name", "code", "is_active"),
    "users": (
        "username",
        "password",
        "nickname",
        "gender",
        "email",
        "phone",
        "first_name",
        "last_name",
        "is_active",
        "is_superuser",
        "date_joined",
        "last_login",
    ),
    "data_permissions": ("name", "rules", "is_active"),
    "system_configs": ("key", "value", "is_active", "access", "inherit"),
    "personal_configs": ("key", "value", "is_active", "access"),
    "files": ("filename", "filesize", "mime_type", "is_upload", "is_tmp", "file_url"),
    "notices": ("title", "message", "level", "notice_type", "publish", "created_time"),
}

# 外键：(目标实体, 目标自然键列, 本表外键列)
FK_MAP = {
    "depts": {"parent": ("depts", "code", "parent_id")},
    "users": {"dept": ("depts", "code", "dept_id")},
    "personal_configs": {"owner": ("users", "username", "owner_id")},
}

# 模型自带文件字段（用户头像等）：随记录导出存储路径，并把物理文件拷进 files/
MODEL_FILE_FIELDS = {"users": ("avatar",)}

READS_TABLE = "notifications_messageuserread"
EXPORT_ORDER = (
    "depts",
    "roles",
    "data_permissions",
    "system_configs",
    "users",
    "personal_configs",
    "files",
    "notices",
)


def connect(dsn):
    """优先 psycopg（v3），回退 psycopg2（旧版本容器里装的是后者）。"""
    try:
        import psycopg  # type: ignore

        return psycopg.connect(dsn)
    except ImportError:
        pass
    try:
        import psycopg2  # type: ignore

        return psycopg2.connect(dsn)
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        raise SystemExit(f"需要 psycopg 或 psycopg2：{exc}") from exc


def jsonable(value):
    """数据库值 → JSON 可写形态。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes__": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return str(value)


class Exporter:
    def __init__(self, cursor, output, media_root, include, warn):
        self.cursor = cursor
        self.output = output
        self.media_root = media_root
        self.include = include
        self.warn = warn
        self.data = {}
        self.keys = {}  # 实体 → {旧主键: 自然键}
        self.raw = {}  # 实体 → [原始行]
        self._link_cache: dict = {}  # 中间表 / 已读表索引缓存

    # ---------- 基础 ----------

    def table_exists(self, table):
        self.cursor.execute(
            "select 1 from information_schema.tables where table_schema = current_schema() and table_name = %s",
            (table,),
        )
        return self.cursor.fetchone() is not None

    def fetch(self, table, columns="*"):
        self.cursor.execute(f"select {columns} from {table}")
        names = [item[0] for item in self.cursor.description]
        return [dict(zip(names, row, strict=False)) for row in self.cursor.fetchall()]

    # ---------- 主流程 ----------

    def run(self):
        for name, (table, key_column) in {**TABLE_MAP, **REFERENCE_TABLES}.items():
            if name not in self.include and name not in REFERENCE_TABLES:
                continue
            if not self.table_exists(table):
                self.warn(f"表 {table} 不存在（实体 {name}），跳过")
                continue
            rows = self.fetch(table)
            self.raw[name] = rows
            self.keys[name] = {row["id"]: str(row.get(key_column) or "") for row in rows}

        for name in EXPORT_ORDER:
            if name not in self.include:
                continue
            if name not in self.raw:
                continue
            self.data[name] = [self._build_row(name, row) for row in self.raw[name]]
            if name == "files":
                self._dump_files()
            self._dump_model_files(name)

    def _build_row(self, name, row):
        out = {}
        for column in SCALAR_COLUMNS[name]:
            if column in row:
                out[column] = jsonable(row[column])
        for field, (target, _, column) in FK_MAP.get(name, {}).items():
            out[field] = self.keys.get(target, {}).get(row.get(column)) or ""
        for entity, field, _, local_column, _, target in M2M_MAP:
            if entity == name:
                out[field] = self._m2m(row, local_column, target)
        if name == "files":
            out["storage_name"] = str(row.get("filepath") or "")
            out["signature"] = f"{row.get('filename')}:{row.get('filesize') or 0}"
        if name == "notices":
            out["reads"] = self._reads(row)
            out["signature"] = f"{row.get('title')}:{jsonable(row.get('created_time')) or ''}"
        if name == "personal_configs":
            out["signature"] = f"{out.get('owner', '')}:{row.get('key') or ''}"
        for field in MODEL_FILE_FIELDS.get(name, ()):
            out[f"{field}_path"] = str(row.get(field) or "")
        out[TABLE_MAP[name][1]] = str(row.get(TABLE_MAP[name][1]) or "")
        return out

    # ---------- 关联 ----------

    def _m2m(self, row, local_column, target):
        mapping = self.keys.get(target)
        if not mapping:
            return []
        _, _, table, _, target_column, _ = next(
            item for item in M2M_MAP if item[3] == local_column and item[5] == target
        )
        index = self._link_index(table, local_column, target_column)
        values = [mapping.get(pk) for pk in index.get(str(row["id"]), [])]
        return sorted({item for item in values if item})

    def _link_index(self, table, local_column, target_column):
        """中间表索引：{本侧主键: [目标侧主键]}（按表缓存，避免逐行扫描）。"""
        cache = self._link_cache.setdefault((table, local_column, target_column), None)
        if cache is None:
            cache = {}
            if self.table_exists(table):
                for link in self.fetch(table, f"{local_column}, {target_column}"):
                    cache.setdefault(str(link[local_column]), []).append(link[target_column])
            self._link_cache[(table, local_column, target_column)] = cache
        return cache

    def _reads(self, row):
        if not self.table_exists(READS_TABLE):
            return []
        users = self.keys.get("users", {})
        index = self._link_cache.get(("reads", "notice_id", "owner_id"))
        if index is None:
            index = {}
            unread_by_link = {}
            for link in self.fetch(READS_TABLE, "notice_id, owner_id, unread"):
                index.setdefault(str(link["notice_id"]), []).append(link["owner_id"])
                unread_by_link[(str(link["notice_id"]), str(link["owner_id"]))] = bool(link["unread"])
            self._link_cache[("reads", "notice_id", "owner_id")] = index
            self._link_cache[("reads_unread", "notice_id", "owner_id")] = unread_by_link
        unread_map = self._link_cache.get(("reads_unread", "notice_id", "owner_id"), {})
        reads = []
        for owner_pk in index.get(str(row["id"]), []):
            owner = users.get(owner_pk)
            if owner:
                reads.append({"owner": owner, "unread": unread_map.get((str(row["id"]), str(owner_pk)), True)})
        return reads

    # ---------- 附件 ----------

    def _dump_files(self):
        files_dir = os.path.join(self.output, FILES_DIR)
        copied = missing = 0
        for row in self.raw.get("files", []):
            storage_name = str(row.get("filepath") or "")
            if not storage_name:
                continue
            source = os.path.join(self.media_root, storage_name) if self.media_root else ""
            target = os.path.join(files_dir, storage_name)
            if not source or not os.path.exists(source):
                missing += 1
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
        if copied or missing:
            print(f"  · 附件：拷贝 {copied} 个，缺失 {missing} 个（缺失只导出记录）")

    def _dump_model_files(self, name):
        """用户头像等模型自带文件字段：把物理文件拷进导出目录。"""
        fields = MODEL_FILE_FIELDS.get(name)
        if not fields:
            return
        files_dir = os.path.join(self.output, FILES_DIR)
        copied = 0
        for row in self.raw.get(name, []):
            for field in fields:
                storage_name = str(row.get(field) or "")
                if not storage_name:
                    continue
                source = os.path.join(self.media_root, storage_name) if self.media_root else ""
                if not source or not os.path.exists(source):
                    continue
                target = os.path.join(files_dir, storage_name)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(source, target)
                copied += 1
        if copied:
            print(f"  · {name} 文件：拷贝 {copied} 个")


def main(argv=None):
    parser = argparse.ArgumentParser(description="导出四域切分前旧库的业务数据（独立脚本）")
    parser.add_argument("--dsn", required=True, help="数据库连接串 postgresql://user:pwd@host:5432/db")
    parser.add_argument("--output", required=True, help="输出目录")
    parser.add_argument("--media-root", default="", help="附件根目录（容器内一般 /data/xadmin-server/data/upload）")
    parser.add_argument("--include", default=",".join(sorted(TABLE_MAP)), help="要导出的实体，逗号分隔")
    args = parser.parse_args(argv)

    def warn(message):
        print(f"  ! {message}", file=sys.stderr)

    os.makedirs(args.output, exist_ok=True)
    include = [item.strip() for item in args.include.split(",") if item.strip()]
    unknown = [item for item in include if item not in TABLE_MAP]
    if unknown:
        raise SystemExit(f"未知实体 {', '.join(unknown)}；可选：{', '.join(EXPORT_ORDER)}")

    connection = connect(args.dsn)
    try:
        exporter = Exporter(connection.cursor(), args.output, args.media_root, include, warn)
        exporter.run()
    finally:
        connection.close()

    payload = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "source_version": "legacy",
        "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "counts": {name: len(rows) for name, rows in exporter.data.items()},
        "data": exporter.data,
    }
    target = os.path.join(args.output, "export.json")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(f"\n导出完成：{target}")
    for name, rows in sorted(exporter.data.items()):
        print(f"  · {name}: {len(rows)} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
