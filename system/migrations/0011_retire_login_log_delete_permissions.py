# -*- coding:utf-8 -*-
"""登录日志只读化：退役「删除 / 批量删除登录日志」权限点。

登录日志 ViewSet 已收口为只读 + 导出 + 强退（不再提供删除端点，O8-4），
对应权限点随之退役：存量库删除这两条权限点与它们的菜单元信息，避免授权树
残留可勾选项、权限审计误报「游离权限点」；与操作日志只读化
（0006_retire_operation_log_delete_permissions）同口径。

反向迁移不重建权限点：能力已下线，重建只会让同一权限点再次出现。
"""

from django.db import migrations

RETIRED_PERMISSION_NAMES = ("destroy:SystemUserLoginLog", "batchDestroy:SystemUserLoginLog")


def retire_login_log_delete_permissions(apps, schema_editor):
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
        ("system", "0010_alter_userloginlog_login_type_and_more"),
    ]

    operations = [
        migrations.RunPython(retire_login_log_delete_permissions, migrations.RunPython.noop),
    ]
