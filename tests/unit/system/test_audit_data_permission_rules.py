# -*- coding: utf-8 -*-
"""巡检命令 audit_data_permission_rules：列出非法授权 + --deactivate / --strict。"""

import pytest
from django.core.management import call_command

from system.models import DataPermission

pytestmark = pytest.mark.django_db


def make_bad_permission(name="巡检-坏规则"):
    """坏规则（字段名写错），与写入侧 validate_rules 口径一致。"""
    return DataPermission.objects.create(
        name=name,
        rules=[{"table": "system.userinfo", "field": "creat0r", "type": "value.text", "value": "x", "match": "exact"}],
    )


def test_audit_reports_invalid_permission(capsys):
    dp = make_bad_permission()
    call_command("audit_data_permission_rules")
    out = capsys.readouterr().out
    assert "[INVALID]" in out
    assert str(dp.pk) in out
    assert "1 invalid permission(s) found" in out


def test_audit_valid_permissions_pass(capsys):
    DataPermission.objects.create(
        name="巡检-合法规则",
        rules=[{"table": "system.userinfo", "field": "id", "type": "value.all", "value": "*", "match": "all"}],
    )
    call_command("audit_data_permission_rules")
    assert "all data permission rules are valid" in capsys.readouterr().out


def test_audit_deactivate(capsys):
    dp = make_bad_permission("巡检-停用")
    call_command("audit_data_permission_rules", "--deactivate")
    dp.refresh_from_db()
    assert dp.is_active is False
    assert "deactivated" in capsys.readouterr().out


def test_audit_strict_exits_non_zero(capsys):
    make_bad_permission("巡检-门禁")
    with pytest.raises(SystemExit) as exc:
        call_command("audit_data_permission_rules", "--strict")
    assert exc.value.code == 1


def test_audit_strict_clean_exits_zero(capsys):
    DataPermission.objects.create(
        name="巡检-门禁-合法",
        rules=[{"table": "system.userinfo", "field": "id", "type": "value.all", "value": "*", "match": "all"}],
    )
    call_command("audit_data_permission_rules", "--strict")
    assert "all data permission rules are valid" in capsys.readouterr().out
