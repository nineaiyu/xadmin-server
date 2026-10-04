# -*- coding: utf-8 -*-
"""审批规则级次审批人校验：四类 assignee 的保存校验与引擎解析一致性。

历史缺陷：``_validate_assignee`` 只特判 ROLE，其余一律按用户名查 UserInfo——
选「岗位」保存必报 "User does not exist"（模型与引擎均支持 post）。
修复后：ROLE / POST / USER 各自按对应实体校验，未知类型 fail-closed 拒绝。
"""

import pytest
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from approval.models.approval_rule import ApprovalRuleLevel
from approval.serializers.approval_rule import ApprovalRuleSerializer
from approval.utils.approval.chains import resolve_level_users
from system.models import Post, UserInfo

pytestmark = pytest.mark.django_db


def _payload(assignee_type, assignee_value, **level_extra):
    level = {
        "name": "第1级",
        "approve_type": "OR",
        "assignee_type": assignee_type,
        "assignee_value": assignee_value,
    }
    level.update(level_extra)
    return {"name": "规则-级次校验", "path_patterns": [r"^/api/test/"], "levels": [level]}


def _validated_serializer(data):
    serializer = ApprovalRuleSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer


class TestAssigneeValidation:
    def test_post_assignee_accepted(self):
        """岗位级次：启用岗位 code 通过校验（修复前必报 User does not exist）。"""
        Post.objects.create(name="安全员", code="rule_post_security")
        serializer = _validated_serializer(_payload("post", "rule_post_security"))
        assert serializer.validated_data["levels"][0]["assignee_type"] == "post"

    def test_post_assignee_unknown_code_rejected(self):
        with pytest.raises(ValidationError) as excinfo:
            _validated_serializer(_payload("post", "no_such_post"))
        assert "Post does not exist" in str(excinfo.value.detail)

    def test_post_assignee_inactive_or_deleted_rejected(self):
        """停用 / 软删除岗位不可作为审批人（与引擎 resolve_level_users 同口径）。"""
        Post.objects.create(name="停用岗", code="rule_post_inactive", is_active=False)
        deleted = Post.objects.create(name="已删岗", code="rule_post_deleted")
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["deleted_at"])
        with pytest.raises(ValidationError):
            _validated_serializer(_payload("post", "rule_post_inactive"))
        with pytest.raises(ValidationError):
            _validated_serializer(_payload("post", "rule_post_deleted"))

    def test_user_and_role_assignees_still_validated(self):
        """USER / ROLE 校验口径不变：未知用户名 / 角色 code 仍拒绝。

        错误文案经 gettext 本地化（测试环境为中文），断言落在插值上。
        """
        with pytest.raises(ValidationError) as user_exc:
            _validated_serializer(_payload("user", "no_such_user"))
        assert "no_such_user" in str(user_exc.value.detail)
        with pytest.raises(ValidationError) as role_exc:
            _validated_serializer(_payload("role", "no_such_role"))
        assert "no_such_role" in str(role_exc.value.detail)

    def test_unknown_assignee_type_rejected(self):
        """未知类型拒绝：级次序列化器 assignee_type 收敛到枚举（fail-closed）。"""
        serializer = ApprovalRuleSerializer(data=_payload("ghost", "anything"))
        assert not serializer.is_valid()
        assert "levels" in serializer.errors


class TestSaveAndResolveConsistency:
    """四类 assignee 保存 → 级次落库 → 引擎解析 一致性。"""

    def test_post_rule_saves_and_resolves_holders(self):
        """选「岗位」保存成功（修复前必报错），引擎按 code 解析出在岗用户。"""
        post = Post.objects.create(name="财务专员", code="rule_post_finance")
        holder = UserInfo.objects.create_user(username="fin_holder", password="Test@123456")
        holder.posts.add(post)

        serializer = _validated_serializer(_payload("post", post.code))
        rule = serializer.save()
        level = rule.levels.get(order=1)
        assert level.assignee_type == ApprovalRuleLevel.AssigneeType.POST
        assert level.assignee_value == post.code
        assert [user.pk for user in resolve_level_users(level)] == [holder.pk]

    def test_user_rule_saves_and_resolves(self):
        UserInfo.objects.create_user(username="lisi", password="Test@123456")
        serializer = _validated_serializer(_payload("user", "lisi"))
        rule = serializer.save()
        level = rule.levels.get(order=1)
        assert [user.username for user in resolve_level_users(level)] == ["lisi"]

    def test_role_rule_saves_and_resolves(self, role):
        """role fixture 的成员经角色 code 解析（引擎按 UserRole.code 口径）。"""
        member = UserInfo.objects.create_user(username="role_member", password="Test@123456")
        member.roles.add(role)
        serializer = _validated_serializer(_payload("role", role.code))
        rule = serializer.save()
        level = rule.levels.get(order=1)
        assert level.assignee_type == ApprovalRuleLevel.AssigneeType.ROLE
        resolved = {user.pk for user in resolve_level_users(level)}
        assert member.pk in resolved

    def test_mixed_multi_level_rule(self):
        """同一规则内 USER + POST 混排级次：两类校验各自生效。"""
        UserInfo.objects.create_user(username="zhangsan", password="Test@123456")
        post = Post.objects.create(name="复核岗", code="rule_post_review")
        serializer = _validated_serializer(
            {
                "name": "混合级次规则",
                "path_patterns": [r"^/api/test/"],
                "levels": [
                    {
                        "name": "初审",
                        "order": 1,
                        "approve_type": "OR",
                        "assignee_type": "user",
                        "assignee_value": "zhangsan",
                    },
                    {
                        "name": "复核",
                        "order": 2,
                        "approve_type": "OR",
                        "assignee_type": "post",
                        "assignee_value": post.code,
                    },
                ],
            }
        )
        rule = serializer.save()
        assert rule.levels.count() == 2
        assert set(rule.levels.values_list("assignee_type", flat=True)) == {"user", "post"}
