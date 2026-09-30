# -*- coding: utf-8 -*-
"""物化筛选列重建命令：存量兼容（旧行无物化值 → 重建后可按新勾选字段筛选）。"""

import pytest
from django.core.management import call_command

from dataset.models.dform import DynamicForm, DynamicFormSubmission

pytestmark = pytest.mark.django_db

SCHEMA_FILTERABLE = {
    "fields": [
        {"key": "name", "label": "姓名", "type": "input", "filterable": True},
        {"key": "level", "label": "级别", "type": "select", "options": ["P4", "P5"], "filterable": True},
        {"key": "remark", "label": "备注", "type": "textarea"},
    ]
}
SCHEMA_PLAIN = {
    "fields": [
        {"key": "name", "label": "姓名", "type": "input"},
        {"key": "level", "label": "级别", "type": "select", "options": ["P4", "P5"]},
    ]
}


def test_rebuild_backfills_legacy_rows(superuser):
    """存量行（物化列引入前落库）重建后可按「可筛选」字段命中；幂等。"""
    form = DynamicForm.objects.create(name="重建表单", schema=SCHEMA_FILTERABLE, creator=superuser)
    legacy = DynamicFormSubmission.objects.create(
        form=form,
        data={"name": "张三", "level": "P5", "remark": None},
        filter_data={},
        creator=superuser,
    )
    fresh = DynamicFormSubmission.objects.create(
        form=form,
        data={"name": "李四", "level": "P4"},
        filter_data={"name": "李四", "level": "P4"},
        creator=superuser,
    )

    call_command("rebuild_dform_filter_data", verbosity=0)
    legacy.refresh_from_db()
    fresh.refresh_from_db()
    assert legacy.filter_data == {"name": "张三", "level": "P5"}
    assert fresh.filter_data == {"name": "李四", "level": "P4"}  # 无差异行不重复写

    # 幂等：再跑一次仍有 0 差异（命令输出为「已重建 0 条」）
    from io import StringIO

    out = StringIO()
    call_command("rebuild_dform_filter_data", stdout=out)
    assert "已重建 0 条" in out.getvalue()


def test_rebuild_dry_run_and_form_scope(superuser):
    """--dry-run 不落库；--form 只重建指定表单；无可筛选字段的表单物化结果为空。"""
    form_a = DynamicForm.objects.create(name="重建表单A", schema=SCHEMA_FILTERABLE, creator=superuser)
    form_b = DynamicForm.objects.create(name="重建表单B", schema=SCHEMA_FILTERABLE, creator=superuser)
    plain_form = DynamicForm.objects.create(name="重建表单C", schema=SCHEMA_PLAIN, creator=superuser)
    row_a = DynamicFormSubmission.objects.create(
        form=form_a, data={"name": "张三", "level": "P5"}, filter_data={}, creator=superuser
    )
    row_b = DynamicFormSubmission.objects.create(
        form=form_b, data={"name": "李四", "level": "P4"}, filter_data={}, creator=superuser
    )
    row_c = DynamicFormSubmission.objects.create(
        form=plain_form, data={"name": "王五"}, filter_data={}, creator=superuser
    )

    call_command("rebuild_dform_filter_data", "--dry-run", verbosity=0)
    for row in (row_a, row_b, row_c):
        row.refresh_from_db()
        assert row.filter_data == {}

    call_command("rebuild_dform_filter_data", "--form", str(form_b.pk), verbosity=0)
    row_a.refresh_from_db()
    row_b.refresh_from_db()
    row_c.refresh_from_db()
    # 仅表单 B 重建；表单 A 未在范围内保持原样；无「可筛选」字段的表单结果为空
    assert row_b.filter_data == {"name": "李四", "level": "P4"}
    assert row_a.filter_data == {}
    assert row_c.filter_data == {}
