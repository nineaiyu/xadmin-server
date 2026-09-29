# -*- coding:utf-8 -*-
"""审计日志只读化：退役「删除 / 批量删除操作日志」权限点。

操作日志 ViewSet 已收口为只读 + 导出（不再提供删除端点），对应权限点随之退役：
存量库删除这两条权限点与它们的菜单元信息，避免授权树残留可勾选项、权限审计
误报「游离权限点」。清理能力的收敛由 ``manage.py log_archive``（归档水位驱动
清理）在服务端统一执行。

反向迁移不重建权限点：能力已下线，重建只会让同一权限点再次出现。
"""

from django.db import migrations

RETIRED_PERMISSION_NAMES = ("destroy:SystemOperationLog", "batchDestroy:SystemOperationLog")


def retire_operation_log_delete_permissions(apps, schema_editor):
    Menu = apps.get_model("system", "Menu")
    MenuMeta = apps.get_model("system", "MenuMeta")
    # 用 _base_manager：历史模型无 all_objects，且软删行（deleted_at 非空）同样要清
    menus = list(Menu._base_manager.filter(name__in=RETIRED_PERMISSION_NAMES))
    if not menus:
        return
    meta_pks = [menu.meta_id for menu in menus if menu.meta_id]
    Menu._base_manager.filter(pk__in=[menu.pk for menu in menus]).delete()
    if meta_pks:
        MenuMeta._base_manager.filter(pk__in=meta_pks).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("system", "0005_post_userinfo_posts_post_uniq_post_name_active_and_more"),
    ]

    operations = [
        migrations.RunPython(retire_operation_log_delete_permissions, migrations.RunPython.noop),
    ]
