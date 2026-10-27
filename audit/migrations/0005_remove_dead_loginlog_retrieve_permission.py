from django.db import migrations

DEAD_PERMISSION = "retrieve:SystemUserLoginLog"


def remove_dead_permission(apps, schema_editor):
    """清理登录日志的 detail retrieve 权限点（视图集无该路由，存量库仍留有种子行）。"""
    # 原地升级在补表前可能尚无该表：数据迁移不是结构前置，缺表时跳过即可（迁移可重入）。
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("select to_regclass(%s)", ("system_menu",))
        if cursor.fetchone()[0] is None:
            return
    Menu = apps.get_model("system", "Menu")
    MenuMeta = apps.get_model("system", "MenuMeta")
    rows = Menu.objects.filter(name=DEAD_PERMISSION)
    meta_ids = [pk for pk in rows.values_list("meta_id", flat=True) if pk]
    rows.delete()
    if meta_ids:
        MenuMeta.objects.filter(pk__in=meta_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0004_operationlog_model_label"),
        ("system", "0002_datapermission_soft_delete"),
    ]

    operations = [
        migrations.RunPython(remove_dead_permission, migrations.RunPython.noop),
    ]
