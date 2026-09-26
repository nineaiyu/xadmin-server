from django.db import migrations

# 3.1 拆分批次2（approval app）：审批流域模型自 system 迁出后的数据平移。
# 约束：表名不变（模型侧显式 db_table）、Menu.path 权限点路径不变、
# 迁移不改任何业务数据——只搬运「以 app_label 为键」的持久化状态：
#   1) django_content_type 行原地改 app_label（auth_permission / GenericFK /
#      打标内容类型全部随之保留，避免角色权限绑定失效）；
#   2) ModelLabelField 模型节点 name（"system.<model>" → "approval.<model>"，
#      仅 parent 为空的模型级行，字段行不受影响）；
#   3) DataPermission.table（同样以 "app_label.model" 记目标）。
APPROVAL_MODELS = [
    "approvalflow",
    "approvalflownode",
    "approvalflowversion",
    "approvalinstance",
    "approvalinstancecomment",
    "approvalnodetask",
    "approvalrequest",
    "approvalrequeststep",
    "approvalrequeststepaction",
    "approvalrule",
    "approvalrulelevel",
    "approvaldelegation",
    "leave",
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


def _rewrite_datapermission_rules(apps, mapping):
    """DataPermission.rules 是 JSONField，行级权限目标以 "table" 键挂在规则项里，
    无法用字段过滤批量改写——逐行 JSON 重写（行数有限，数据权限配置量级）。"""
    DataPermission = apps.get_model("system", "DataPermission")
    for row in DataPermission.objects.all().iterator():
        new_rules = _rewrite_rules_node(row.rules, mapping)
        if new_rules != row.rules:
            row.rules = new_rules
            row.save(update_fields=["rules"])


def forwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="system", model__in=APPROVAL_MODELS).update(app_label="approval")

    ModelLabelField = apps.get_model("system", "ModelLabelField")
    for model in APPROVAL_MODELS:
        ModelLabelField.objects.filter(parent__isnull=True, name=f"system.{model}").update(name=f"approval.{model}")

    _rewrite_datapermission_rules(apps, {f"system.{m}": f"approval.{m}" for m in APPROVAL_MODELS})


def backwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.filter(app_label="approval", model__in=APPROVAL_MODELS).update(app_label="system")

    ModelLabelField = apps.get_model("system", "ModelLabelField")
    for model in APPROVAL_MODELS:
        ModelLabelField.objects.filter(parent__isnull=True, name=f"approval.{model}").update(name=f"system.{model}")

    _rewrite_datapermission_rules(apps, {f"approval.{m}": f"system.{m}" for m in APPROVAL_MODELS})


class Migration(migrations.Migration):
    dependencies = [
        ("system", "0005_squashed_0013_ai_usage_track"),
        ("approval", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
