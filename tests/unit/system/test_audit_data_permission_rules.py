# -*- coding: utf-8 -*-
"""巡检命令 audit_data_permission_rules：非法授权 + 不生效提示 + --deactivate / --strict。"""

import pytest
from django.core.management import call_command

from system.models import DataPermission, DeptInfo

pytestmark = pytest.mark.django_db


def make_bad_permission(name="巡检-坏规则"):
    """坏规则（字段名写错），与写入侧 validate_rules 口径一致。"""
    return DataPermission.objects.create(
        name=name,
        rules=[{"table": "system.userinfo", "field": "creat0r", "type": "value.text", "value": "x", "match": "exact"}],
    )


def make_all_permission(name="巡检-全部数据"):
    return DataPermission.objects.create(
        name=name,
        rules=[{"table": "system.userinfo", "field": "id", "type": "value.all", "value": "*", "match": "all"}],
    )


def make_leader_permission(name="巡检-主管规则"):
    return DataPermission.objects.create(
        name=name,
        rules=[
            {
                "table": "system.userinfo",
                "field": "creator",
                "type": "value.leader.user.ids",
                "value": "*",
                "match": "in",
            }
        ],
    )


def test_audit_reports_invalid_permission(capsys):
    dp = make_bad_permission()
    call_command("audit_data_permission_rules")
    out = capsys.readouterr().out
    assert "[INVALID]" in out
    assert str(dp.pk) in out
    assert "1 invalid permission(s) found" in out


def test_audit_valid_permissions_pass(capsys):
    make_all_permission("巡检-合法规则")
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
    make_all_permission("巡检-门禁-合法")
    call_command("audit_data_permission_rules", "--strict")
    assert "all data permission rules are valid" in capsys.readouterr().out


# ---------- 不生效提示（[WARN]，不参与 --strict 判定）----------


def test_audit_warns_unbound_permission(capsys):
    make_all_permission("巡检-未绑定")
    call_command("audit_data_permission_rules")
    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "未绑定任何用户或部门" in out


def test_audit_warns_leader_rule_bound_to_non_leader(capsys, normal_user):
    dp = make_leader_permission()
    dp.userinfo_set.add(normal_user)

    call_command("audit_data_permission_rules")

    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "没有任何部门主管" in out


def test_audit_leader_rule_with_real_leader_has_no_warning(capsys, normal_user):
    DeptInfo.objects.create(name="巡检部", code="audit-leader-dept", leader=normal_user)
    dp = make_leader_permission("巡检-主管就绪")
    dp.userinfo_set.add(normal_user)

    call_command("audit_data_permission_rules")

    out = capsys.readouterr().out
    assert "[WARN]" not in out
    assert "all data permission rules are valid" in out


def test_audit_warns_dangling_reference(capsys, normal_user):
    missing = "00000000-0000-0000-0000-000000000000"
    dp = DataPermission.objects.create(
        name="巡检-悬空引用",
        rules=[
            {
                "table": "system.userinfo",
                "field": "dept",
                "type": "value.table.dept.ids",
                "value": [missing],
                "match": "in",
            }
        ],
    )
    dp.userinfo_set.add(normal_user)  # 绑定真实用户，隔离「未绑定」提示

    call_command("audit_data_permission_rules")

    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "引用的对象已不存在" in out
    assert missing in out


def test_audit_warnings_do_not_fail_strict(capsys):
    make_all_permission("巡检-只有提示")
    call_command("audit_data_permission_rules", "--strict")
    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "all data permission rules are valid" in out
