# 操作日志 icontains 检索字段补 pg_trgm GIN 索引（audit 域第三个迁移）。
#
# 背景：操作日志页的 path/module/system 过滤框走 icontains，controlled_lookup
# 亦允许任意声明字段 __icontains 透传；ILIKE '%x%' 用不上 B-tree
# （idx_oplog_path 仅服务精确/前缀过滤），高容量表（保留 180-365 天，百万行级）
# 上每次过滤都是全表扫描。对齐 UserInfo 既有 trgm 范式
# （identity/models/user.py 的 GinIndex(gin_trgm_ops)）。
# ipaddress 是 inet 类型（gin_trgm_ops 不支持 inet），补普通 B-tree 服务精确过滤。
#
# 索引创建对既有大表是耗时操作（CONCURRENTLY 需事务外执行，迁移期可接受短暂
# 锁写；升级窗口执行，见 docs/ops/upgrade-stock.md）。

import logging

from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models

logger = logging.getLogger(__name__)


def _ensure_trgm_extension(apps, schema_editor):
    """CREATE EXTENSION IF NOT EXISTS pg_trgm（幂等；identity/0001 通常已建）。"""
    if schema_editor.connection.vendor != "postgresql":
        return
    from system.search_indexes import TRGM_EXTENSION_SQL

    try:
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(TRGM_EXTENSION_SQL)
    except Exception:  # noqa: BLE001 扩展不可用不阻断迁移（检索回退顺序扫描）
        logger.warning("pg_trgm unavailable; trgm indexes will be skipped", exc_info=True)


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0002_initial"),
        # identity/0001 首操作创建 pg_trgm 扩展，显式依赖保证先序
        ("identity", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(_ensure_trgm_extension, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="operationlog",
            index=GinIndex(fields=["path"], name="idx_oplog_path_trgm", opclasses=["gin_trgm_ops"]),
        ),
        migrations.AddIndex(
            model_name="operationlog",
            index=GinIndex(fields=["module"], name="idx_oplog_module_trgm", opclasses=["gin_trgm_ops"]),
        ),
        migrations.AddIndex(
            model_name="operationlog",
            index=GinIndex(fields=["system"], name="idx_oplog_system_trgm", opclasses=["gin_trgm_ops"]),
        ),
        migrations.AddIndex(
            model_name="operationlog",
            index=models.Index(fields=["ipaddress"], name="idx_oplog_ip"),
        ),
    ]
