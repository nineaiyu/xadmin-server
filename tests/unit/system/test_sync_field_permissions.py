# -*- coding: utf-8 -*-
"""字段白名单一键同步：新增字段只增不减地补进已有角色白名单。"""

from io import StringIO

import pytest
from django.core.management import call_command

from identity.models import UserRole
from system.models import FieldPermission, ModelLabelField
from system.services.permission_sync import complete_field_permissions

pytestmark = pytest.mark.django_db


@pytest.fixture()
def field_scope(menu_factory):
    """一个绑定 messagecontent ROLE 字段树的权限点 + 角色 + 已有白名单。"""
    root = ModelLabelField.objects.create(
        name="notifications.messagecontent",
        label="消息中心",
        field_type=ModelLabelField.FieldChoices.ROLE,
    )
    message = ModelLabelField.objects.create(
        name="message",
        label="通知内容",
        parent=root,
        field_type=ModelLabelField.FieldChoices.ROLE,
    )
    message_format = ModelLabelField.objects.create(
        name="message_format",
        label="正文格式",
        parent=root,
        field_type=ModelLabelField.FieldChoices.ROLE,
    )
    # 真实存在的历史字段，但白名单里刻意没有：模拟管理员裁剪，命令不得恢复它。
    extra_json = ModelLabelField.objects.create(
        name="extra_json",
        label="额外的Json数据",
        parent=root,
        field_type=ModelLabelField.FieldChoices.ROLE,
    )
    menu = menu_factory("list:SystemNoticeTest", path="api/notifications/notice-messages$", method="GET")
    menu.model.add(root)
    role = UserRole.objects.create(name="字段权限测试", code="field_perm_test")
    role.menu.add(menu)
    permission = FieldPermission.objects.create(role=role, menu=menu)
    permission.field.set([message])
    return role, menu, permission, message, message_format, extra_json


class TestCompleteFieldPermissions:
    def test_adds_missing_new_field_without_removing_existing(self, field_scope):
        role, menu, permission, message, message_format, _extra_json = field_scope

        completed = complete_field_permissions(roles=[role])

        assert len(completed) == 1
        assert completed[0][0] == role and completed[0][1] == menu
        names = set(permission.field.values_list("name", flat=True))
        assert names == {"message", "message_format", "extra_json"}
        # 只增不减：原有字段仍在
        assert message in permission.field.all()

    def test_dry_run_reports_without_writing(self, field_scope):
        role, _menu, permission, _message, message_format, _extra_json = field_scope
        permission.field.remove(message_format)

        completed = complete_field_permissions(roles=[role], dry_run=True)

        assert len(completed) == 1
        assert message_format in completed[0][2]
        assert not permission.field.filter(pk=message_format.pk).exists()

    def test_command_dry_run_reports_missing_field(self, field_scope):
        _role, _menu, permission, _message, message_format, _extra_json = field_scope
        permission.field.remove(message_format)
        # 模拟「代码新增字段、尚未重建字段树」：删除树节点，命令应在事务内重建后回滚
        message_format.delete()

        stdout = StringIO()
        call_command("sync_field_permissions", "--dry-run", stdout=stdout)

        assert "message_format" in stdout.getvalue()
        assert not permission.field.filter(name="message_format").exists()
        assert not ModelLabelField.objects.filter(
            name="message_format", parent__name="notifications.messagecontent"
        ).exists()

    def test_command_syncs_existing_whitelists(self, field_scope):
        _role, _menu, permission, _message, message_format, _extra_json = field_scope
        permission.field.remove(message_format)
        message_format.delete()

        stdout = StringIO()
        call_command("sync_field_permissions", stdout=stdout)

        assert "同步完成" in stdout.getvalue()
        assert permission.field.filter(name="message_format").exists()
        # 安全边界：历史字段 extra_json 虽然在字段树里，但白名单刻意没有 → 不得自动恢复
        assert not permission.field.filter(name="extra_json").exists()
