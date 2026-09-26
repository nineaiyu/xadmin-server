from django.db import migrations

# 3.1 拆分批次4（dataset app）：数据分析与动态表单模型自 system 迁出后的数据平移。
# 表名不变（模型侧显式 db_table）、路由/权限点路径不变——只搬运
# 「以 app_label 为键」的持久化状态：
#   1) django_content_type 行原地改 app_label；
#   2) ModelLabelField 模型节点 name（"system.<model>" → "dataset.<model>"）；
#   3) DataPermission.rules 内 table 目标（JSON 递归重写）。
DATASET_MODELS = [
    "dashboard",
    "dataset",
    "dynamicform",
    "dynamicformsubmission",
    "report",
    "screen",
]


def _rewrite_rules_node(node, mapping):
    """递归重写规则 JSON 里的 "table" 目标（app_label.model 串）。"""
    if isinstance(node, list):
        return [_rewrite_rules_node(item, mapping) for item in node]
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "table" and isinstance(value, str) and value in mapping:
                out[key] = mapping[value]
            else:
                out[key] = _rewrite_rules_node(value, mapping)
        return out
    return node


def forwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="system", model__in=DATASET_MODELS).update(app_label="dataset")

    ModelLabelField = apps.get_model("system", "ModelLabelField")
    for model in DATASET_MODELS:
        ModelLabelField.objects.filter(parent__isnull=True, name=f"system.{model}").update(name=f"dataset.{model}")

    DataPermission = apps.get_model("system", "DataPermission")
    mapping = {f"system.{m}": f"dataset.{m}" for m in DATASET_MODELS}
    for row in DataPermission.objects.all().iterator():
        new_rules = _rewrite_rules_node(row.rules, mapping)
        if new_rules != row.rules:
            row.rules = new_rules
            row.save(update_fields=["rules"])


def backwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="dataset", model__in=DATASET_MODELS).update(app_label="system")

    ModelLabelField = apps.get_model("system", "ModelLabelField")
    for model in DATASET_MODELS:
        ModelLabelField.objects.filter(parent__isnull=True, name=f"dataset.{model}").update(name=f"system.{model}")


class Migration(migrations.Migration):
    dependencies = [
        ("system", "0018_dataset_split_state"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
