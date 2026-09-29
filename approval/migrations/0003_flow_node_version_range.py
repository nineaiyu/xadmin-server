# -*- coding:utf-8 -*-
"""审批流在途实例绑版本：节点有效区间（见 docs/adr/ADR-073-in-flight-flow-versioning.md）。

- 节点行新增 ``version_from`` / ``version_to``：改版不再物理删除节点，而是在事务内
  收口当前生效行（``version_to = 新版本``）+ 按新版本落新行，实例按自身
  ``flow_version`` 过滤节点集，在途单不再被「存在 PENDING 实例」锁死改版；
- ``(flow, order)`` 无条件唯一 → 条件唯一（仅当前生效行之间唯一）。MySQL/MariaDB
  不支持部分索引（``supports_partial_indexes=False``），该约束在那些引擎上不落库，
  由写入路径的流程行锁 + 「先收口再落行」保证同一时刻只有一个当前行——以
  PostgreSQL（默认部署形态）为准；
- 存量兼容：存量节点行随列默认值标为「自 v1 起生效、至今有效」，存量在途实例在
  部署后首次改版前看到的节点集与改造前一致；``flow.version < 1`` 回填为 1，
  保证实例钉住版本总能命中生效行。
"""

from django.conf import settings
from django.db import migrations, models


def backfill_flow_versions(apps, schema_editor):
    """flow.version < 1（历史脏数据 / 早期种子）回填为 1。"""
    ApprovalFlow = apps.get_model("approval", "ApprovalFlow")
    ApprovalFlow._base_manager.filter(version__lt=1).update(version=1)


class Migration(migrations.Migration):
    dependencies = [
        ("approval", "0002_alter_approvalflownode_assignee_type_and_more"),
        ("system", "0007_apiapplication_daily_quota_hard"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="approvalflownode",
            name="uniq_approval_flow_node_order",
        ),
        migrations.AddField(
            model_name="approvalflownode",
            name="version_from",
            field=models.IntegerField(default=1, verbose_name="Effective from version"),
        ),
        migrations.AddField(
            model_name="approvalflownode",
            name="version_to",
            field=models.IntegerField(blank=True, default=None, null=True, verbose_name="Effective until version"),
        ),
        migrations.AddConstraint(
            model_name="approvalflownode",
            constraint=models.UniqueConstraint(
                condition=models.Q(("version_to__isnull", True)),
                fields=("flow", "order"),
                name="uniq_flow_node_order_active",
            ),
        ),
        migrations.RunPython(backfill_flow_versions, migrations.RunPython.noop),
    ]
