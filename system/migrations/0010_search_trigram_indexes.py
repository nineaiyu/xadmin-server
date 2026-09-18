# -*- coding:utf-8 -*-
"""全局搜索 pg_trgm 索引（PostgreSQL 生效，其它后端跳过）。

- **state 侧**：`AddIndex` 只写模型状态（与四个模型的 Meta 一致），保证
  `makemigrations` 不再重复生成；
- **database 侧**：`RunPython` 按下面的快照建索引——仅 PostgreSQL 执行，
  扩展不可用 / 单索引失败只告警不抛出（性能优化不阻断部署，检索仍然正确，
  只退回顺序扫描）；回滚只删索引、不动 pg_trgm 扩展（可能被其它用途共享）；
- 索引清单、豁免理由与验证方式见 docs/architecture/indexes.md 与
  system/search_indexes.py；快照与运行期清单的漂移由
  tests/unit/system/test_search_indexes.py 守护。

验证（库上手工核对）：
`EXPLAIN SELECT id FROM system_userinfo WHERE username ILIKE '%关键词%';`
应出现 `Bitmap Index Scan on idx_userinfo_username_trgm`。
"""

from django.contrib.postgres.indexes import GinIndex
from django.db import migrations

from common.utils import get_logger
from system.search_indexes import TRGM_EXTENSION_SQL, create_index_sql, drop_index_sql

logger = get_logger(__name__)

# 建索引快照（索引名, 表, 字段）：与 system/search_indexes.py::SEARCH_TRGM_INDEXES 一致；
# 迁移内冻结，后续新增字段走新迁移（不回溯改变本迁移的行为）
TRGM_INDEXES = (
    ("idx_userinfo_username_trgm", "system_userinfo", "username"),
    ("idx_userinfo_nickname_trgm", "system_userinfo", "nickname"),
    ("idx_userinfo_email_trgm", "system_userinfo", "email"),
    ("idx_userinfo_phone_trgm", "system_userinfo", "phone"),
    ("idx_uploadfile_filename_trgm", "system_uploadfile", "filename"),
    ("idx_approvalrequest_path_trgm", "system_approvalrequest", "path"),
    ("idx_approvalrequest_module_trgm", "system_approvalrequest", "module"),
    ("idx_approvalrequest_object_pk_trgm", "system_approvalrequest", "object_pk"),
    ("idx_leave_reason_trgm", "system_leave", "reason"),
)


def _create_indexes(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute(TRGM_EXTENSION_SQL)
    except Exception:  # noqa: BLE001 扩展不可用不阻断迁移：检索回退顺序扫描
        logger.warning("pg_trgm unavailable; global search falls back to sequential scan", exc_info=True)
        return
    for name, table, field in TRGM_INDEXES:
        try:
            with connection.cursor() as cursor:
                cursor.execute(create_index_sql(table, field, name))
        except Exception:  # noqa: BLE001 单索引失败不影响其余索引
            logger.warning("create search trigram index failed: %s", name, exc_info=True)


def _drop_indexes(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    for name, _table, _field in TRGM_INDEXES:
        with connection.cursor() as cursor:
            cursor.execute(drop_index_sql(name))


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("system", "0009_report_notify_channels"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name="approvalrequest",
                    index=GinIndex(fields=["path"], name="idx_approvalrequest_path_trgm", opclasses=["gin_trgm_ops"]),
                ),
                migrations.AddIndex(
                    model_name="approvalrequest",
                    index=GinIndex(
                        fields=["module"], name="idx_approvalrequest_module_trgm", opclasses=["gin_trgm_ops"]
                    ),
                ),
                migrations.AddIndex(
                    model_name="approvalrequest",
                    index=GinIndex(
                        fields=["object_pk"], name="idx_approvalrequest_object_pk_trgm", opclasses=["gin_trgm_ops"]
                    ),
                ),
                migrations.AddIndex(
                    model_name="leave",
                    index=GinIndex(fields=["reason"], name="idx_leave_reason_trgm", opclasses=["gin_trgm_ops"]),
                ),
                migrations.AddIndex(
                    model_name="uploadfile",
                    index=GinIndex(
                        fields=["filename"], name="idx_uploadfile_filename_trgm", opclasses=["gin_trgm_ops"]
                    ),
                ),
                migrations.AddIndex(
                    model_name="userinfo",
                    index=GinIndex(fields=["username"], name="idx_userinfo_username_trgm", opclasses=["gin_trgm_ops"]),
                ),
                migrations.AddIndex(
                    model_name="userinfo",
                    index=GinIndex(fields=["nickname"], name="idx_userinfo_nickname_trgm", opclasses=["gin_trgm_ops"]),
                ),
                migrations.AddIndex(
                    model_name="userinfo",
                    index=GinIndex(fields=["email"], name="idx_userinfo_email_trgm", opclasses=["gin_trgm_ops"]),
                ),
                migrations.AddIndex(
                    model_name="userinfo",
                    index=GinIndex(fields=["phone"], name="idx_userinfo_phone_trgm", opclasses=["gin_trgm_ops"]),
                ),
            ],
            database_operations=[
                migrations.RunPython(_create_indexes, _drop_indexes),
            ],
        ),
    ]
