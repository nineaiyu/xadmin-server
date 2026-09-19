# -*- coding: utf-8 -*-
"""回填内置种子行缺失的创建/更新时间。

``loaddata`` 以 raw 方式保存（``save_base(raw=True)`` 跳过 ``pre_save``），
``auto_now_add``/``auto_now`` 不生效：种子 JSON 未显式给出时间的行落库为 NULL，
列表页「更新时间」列整列空白（dataset / screen / report / fieldpermission 等）。
本迁移对内置种子涉及的模型做一次性回填，只更新 NULL 行，不动已有时间。

``load_init_json`` 已同步内置回填逻辑（新导入的数据不会再产生 NULL 行）；
本迁移用于给存量库补齐历史行。
"""

from django.db import migrations
from django.utils import timezone

# 与 load_init_json.Command.model_names 同源（种子涉及的全部模型）
SEED_MODEL_LABELS = [
    "system.menumeta",
    "system.menu",
    "system.systemconfig",
    "system.datadict",
    "system.datapermission",
    "system.userrole",
    "system.fieldpermission",
    "system.modellabelfield",
    "system.deptinfo",
    "settings.setting",
    "system.dataset",
    "system.dashboard",
    "system.screen",
    "system.report",
    "system.datamaskrule",
    "system.approvalflow",
    "system.approvalflownode",
    "system.approvalflowversion",
    "system.dynamicform",
    "system.dynamicformsubmission",
]


def backfill_null_timestamps(apps, schema_editor):
    now = timezone.now()
    db_alias = schema_editor.connection.alias
    for label in SEED_MODEL_LABELS:
        try:
            model = apps.get_model(label)
        except LookupError:
            # 跨 app 模型在依赖未就绪时跳过（对应表尚无数据可回填）
            continue
        field_names = {field.name for field in model._meta.concrete_fields}
        queryset = model._default_manager.using(db_alias)
        if "created_time" in field_names:
            queryset.filter(created_time__isnull=True).update(created_time=now)
        if "updated_time" in field_names:
            queryset.filter(updated_time__isnull=True).update(updated_time=now)


class Migration(migrations.Migration):
    dependencies = [
        ("system", "0006_moduleoverride"),
    ]

    operations = [
        migrations.RunPython(backfill_null_timestamps, migrations.RunPython.noop),
    ]
