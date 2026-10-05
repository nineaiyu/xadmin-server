# -*- coding: utf-8 -*-
"""审批规则级次的更新语义：按 order upsert（不再 delete+recreate）。

历史缺陷：编辑规则时级次整体删除重建——级次行主键与创建审计（created_time/
created_by）每次编辑都被重置，审批痕迹审计不可信。upsert 后 order 即键：
同 order 原位更新、缺失删除、新增创建，行主键跨编辑稳定。
"""

import pytest

from approval.models.approval_rule import ApprovalRule
from approval.serializers.approval_rule import ApprovalRuleSerializer

pytestmark = pytest.mark.django_db

# 级次审批人必须是真实可用账号（保存校验与引擎解析同口径），先建人再配规则
_APPROVER_NAMES = ("alice", "bob", "carol", "dave")


@pytest.fixture(autouse=True)
def _approvers():
    from identity.models import UserInfo

    users = [UserInfo.objects.create_user(username=name, password="Test@123456") for name in _APPROVER_NAMES]
    yield users


def _create_rule() -> ApprovalRule:
    serializer = ApprovalRuleSerializer(
        data={
            "name": "upsert-规则",
            "path_patterns": [r"^/api/test/"],
            "levels": [
                {"order": 1, "name": "初审", "approve_type": "OR", "assignee_type": "user", "assignee_value": "alice"},
                {"order": 2, "name": "复核", "approve_type": "AND", "assignee_type": "user", "assignee_value": "bob"},
            ],
        }
    )
    serializer.is_valid(raise_exception=True)
    return serializer.save()


def _update_rule(rule: ApprovalRule, levels: list) -> ApprovalRule:
    serializer = ApprovalRuleSerializer(
        rule, data={"name": rule.name, "path_patterns": rule.path_patterns, "levels": levels}, partial=True
    )
    serializer.is_valid(raise_exception=True)
    return serializer.save()


class TestLevelUpsert:
    def test_edit_keeps_level_pks_stable(self):
        """编辑不改 order 的级次：行主键与创建审计保持不变（不再删除重建）。"""
        rule = _create_rule()
        before = {level.order: (level.pk, level.created_time) for level in rule.levels.all()}

        _update_rule(
            rule,
            [
                {
                    "order": 1,
                    "name": "初审(改)",
                    "approve_type": "OR",
                    "assignee_type": "user",
                    "assignee_value": "carol",
                },
                {"order": 2, "name": "复核", "approve_type": "AND", "assignee_type": "user", "assignee_value": "bob"},
            ],
        )
        after = {level.order: (level.pk, level.created_time) for level in rule.levels.all()}
        assert before.keys() == after.keys()
        for order in before:
            assert before[order] == after[order]
        assert rule.levels.get(order=1).assignee_value == "carol"

    def test_edit_removes_and_adds_levels(self):
        """级次删减/新增：未被本次提交引用的既有行删除，新 order 创建新行。"""
        rule = _create_rule()
        old_second_pk = rule.levels.get(order=2).pk

        _update_rule(
            rule,
            [
                {"order": 1, "name": "初审", "approve_type": "OR", "assignee_type": "user", "assignee_value": "alice"},
                {"order": 3, "name": "终审", "approve_type": "OR", "assignee_type": "user", "assignee_value": "dave"},
            ],
        )
        orders = set(rule.levels.values_list("order", flat=True))
        assert orders == {1, 3}
        assert not rule.levels.filter(pk=old_second_pk).exists()
        assert rule.levels.get(order=3).name == "终审"

    def test_levels_omitted_leaves_rows_untouched(self):
        """不传 levels 的部分更新不动级次（重命名等场景零副作用）。"""
        rule = _create_rule()
        before = set(rule.levels.values_list("pk", flat=True))

        serializer = ApprovalRuleSerializer(rule, data={"name": "改名"}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        assert set(rule.levels.values_list("pk", flat=True)) == before
