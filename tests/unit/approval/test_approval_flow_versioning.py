# -*- coding: utf-8 -*-
"""审批流在途实例绑版本（节点有效区间）：改版解锁 / 旧单走旧版 / 回滚隔离 / 区间查询。

语义与边界见 docs/adr/ADR-073-in-flight-flow-versioning.md。
"""

import pytest
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _gettext

from approval.models.approval import ApprovalFlowNode, ApprovalInstance, ApprovalNodeTask
from approval.serializers.approval_flow import ApprovalFlowSerializer
from approval.utils.approval_flow import approve_task, create_instance
from system.models import UserInfo, UserRole

pytestmark = pytest.mark.django_db

NODE_CORE = {"assignee_type": "role", "assignee_value": "flow_approver"}


@pytest.fixture
def approver_role(db):
    return UserRole.objects.create(name="审批人", code="flow_approver")


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_user(username="flow_ver_applicant", password="Test@123456", nickname="申请人")


@pytest.fixture
def approver(approver_role):
    user = UserInfo.objects.create_user(username="flow_ver_approver", password="Test@123456", nickname="审批人")
    user.roles.add(approver_role)
    return user


def publish(code, nodes):
    """经序列化器发布流程（v1 + 初始快照 + 生效节点行），与 UI 保存同口径。"""
    return ApprovalFlowSerializer().create({"name": f"流程-{code}", "code": code, "nodes": nodes})


def update_nodes(flow, nodes):
    return ApprovalFlowSerializer().update(flow, {"nodes": nodes})


def approve_current(instance, node_order):
    """通过某节点序号的待办任务（approver fixture 唯一处理人）。"""
    task = ApprovalNodeTask.objects.get(
        instance=instance, node_order=node_order, status=ApprovalNodeTask.Status.PENDING
    )
    ok, detail = approve_task(task.pk, task.assignee)
    instance.refresh_from_db()
    return ok, detail


class TestInFlightVersioning:
    def test_edit_with_pending_instance_old_and_new_run_apart(self, applicant, approver):
        """核心演练：改版后在途单走旧版（两节点），新单走新版（三节点）。"""
        flow = publish(
            "ver_split", [{"name": "初审", "order": 1, **NODE_CORE}, {"name": "终审", "order": 2, **NODE_CORE}]
        )
        old_instance, error = create_instance(flow=flow, applicant=applicant, title="旧单", form_data={})
        assert error is None
        assert old_instance.flow_version == 1

        # 改版：追加第三节点（有在途单也允许）
        flow = update_nodes(
            flow,
            [
                {"name": "初审", "order": 1, **NODE_CORE},
                {"name": "终审", "order": 2, **NODE_CORE},
                {"name": "归档", "order": 3, **NODE_CORE},
            ],
        )
        assert flow.version == 2
        assert flow.nodes.count() == 3

        # 旧单沿 v1 走：初审 → 终审 → 通过（不经过新增的归档节点）
        assert approve_current(old_instance, 1)[0] is True
        assert old_instance.current_node.order == 2
        assert approve_current(old_instance, 2)[0] is True
        assert old_instance.status == ApprovalInstance.Status.APPROVED
        assert old_instance.tasks.filter(node_order=3).exists() is False

        # 新单沿 v2 走：初审 → 终审 → 归档
        new_instance, error = create_instance(flow=flow, applicant=applicant, title="新单", form_data={})
        assert error is None
        assert new_instance.flow_version == 2
        assert approve_current(new_instance, 1)[0] is True
        assert approve_current(new_instance, 2)[0] is True
        assert new_instance.current_node.order == 3
        assert approve_current(new_instance, 3)[0] is True
        assert new_instance.status == ApprovalInstance.Status.APPROVED

    def test_node_removed_in_new_version_does_not_block_old_instance(self, applicant, approver):
        """新版删掉末节点：旧单仍按旧版走到该节点并通过；新单在首节点即终态。"""
        flow = publish(
            "ver_removed", [{"name": "初审", "order": 1, **NODE_CORE}, {"name": "终审", "order": 2, **NODE_CORE}]
        )
        old_instance, error = create_instance(flow=flow, applicant=applicant, title="旧单", form_data={})
        assert error is None

        flow = update_nodes(flow, [{"name": "初审", "order": 1, **NODE_CORE}])
        assert flow.version == 2

        assert approve_current(old_instance, 1)[0] is True
        assert old_instance.current_node.order == 2  # 仍进入旧版末节点
        assert approve_current(old_instance, 2)[0] is True
        assert old_instance.status == ApprovalInstance.Status.APPROVED

        new_instance, error = create_instance(flow=flow, applicant=applicant, title="新单", form_data={})
        assert error is None
        assert approve_current(new_instance, 1)[0] is True
        assert new_instance.status == ApprovalInstance.Status.APPROVED

    def test_rollback_isolated_from_in_flight(self, applicant, approver):
        """回滚只影响之后的单：在途单仍按钉住版本推进。"""
        flow = publish(
            "ver_rollback_iso", [{"name": "初审", "order": 1, **NODE_CORE}, {"name": "终审", "order": 2, **NODE_CORE}]
        )
        old_instance, error = create_instance(flow=flow, applicant=applicant, title="旧单", form_data={})
        assert error is None

        flow = update_nodes(
            flow,
            [
                {"name": "初审", "order": 1, **NODE_CORE},
                {"name": "终审", "order": 2, **NODE_CORE},
                {"name": "归档", "order": 3, **NODE_CORE},
            ],
        )
        ok, detail = ApprovalFlowSerializer().rollback_to_version(flow, 1)
        assert ok is True and not detail
        assert flow.nodes.count() == 2

        assert approve_current(old_instance, 1)[0] is True
        assert old_instance.current_node.order == 2
        assert approve_current(old_instance, 2)[0] is True
        assert old_instance.status == ApprovalInstance.Status.APPROVED

    def test_legacy_instance_without_version_uses_current_definition(self, applicant, approver):
        """flow_version 为空的历史行回退「当前生效定义」（与绑版本改造前一致）。"""
        flow = publish(
            "ver_legacy", [{"name": "初审", "order": 1, **NODE_CORE}, {"name": "终审", "order": 2, **NODE_CORE}]
        )
        instance, error = create_instance(flow=flow, applicant=applicant, title="旧单", form_data={})
        assert error is None
        ApprovalInstance.objects.filter(pk=instance.pk).update(flow_version=None)

        # 新版只留首节点：无钉住版本 → 按当前定义，通过首节点即终态
        update_nodes(flow, [{"name": "初审", "order": 1, **NODE_CORE}])
        instance.refresh_from_db()
        assert approve_current(instance, 1)[0] is True
        assert instance.status == ApprovalInstance.Status.APPROVED


class TestEffectiveRangeQueries:
    def test_effective_at_across_versions(self, applicant):
        """有效区间查询：v1 / v2 / v3 各版本节点集互相隔离，默认管理器只见当前行。"""
        flow = publish("ver_range", [{"name": "A", "order": 1, **NODE_CORE}])
        flow = update_nodes(flow, [{"name": "A", "order": 1, **NODE_CORE}, {"name": "B", "order": 2, **NODE_CORE}])
        flow = update_nodes(
            flow,
            [
                {"name": "A", "order": 1, **NODE_CORE},
                {"name": "B", "order": 2, **NODE_CORE},
                {"name": "C", "order": 3, **NODE_CORE},
            ],
        )
        all_nodes = ApprovalFlowNode.all_objects.filter(flow=flow)
        assert [node.order for node in all_nodes.effective_at(1).order_by("order")] == [1]
        assert [node.order for node in all_nodes.effective_at(2).order_by("order")] == [1, 2]
        assert [node.order for node in all_nodes.effective_at(3).order_by("order")] == [1, 2, 3]
        # None = 当前生效定义
        assert [node.order for node in all_nodes.effective_at().order_by("order")] == [1, 2, 3]
        # 默认管理器（flow.nodes）只返回当前生效行；历史行仍在 all_objects
        assert flow.nodes.count() == 3
        assert all_nodes.count() == 6  # 1 + 2 + 3

    def test_active_rows_unique_per_order(self, applicant):
        """条件唯一：当前生效行之间 (flow, order) 唯一；历史行与当前行可同 order 共存。"""
        flow = publish("ver_unique", [{"name": "A", "order": 1, **NODE_CORE}])
        with pytest.raises(IntegrityError), transaction.atomic():
            ApprovalFlowNode.objects.create(flow=flow, name="重复", order=1, **NODE_CORE)

        node_pk = flow.nodes.get().pk
        ApprovalFlowNode.all_objects.filter(pk=node_pk).update(version_to=2)
        ApprovalFlowNode.objects.create(flow=flow, name="新行", order=1, **NODE_CORE)
        assert flow.nodes.count() == 1
        assert ApprovalFlowNode.all_objects.filter(flow=flow).count() == 2

    def test_no_change_save_keeps_rows(self, applicant):
        """节点内容无变化：不落新版本、节点行不重建（主键保持）。"""
        nodes = [{"name": "A", "order": 1, **NODE_CORE}]
        flow = publish("ver_nochange", nodes)
        pks = list(flow.nodes.values_list("pk", flat=True))
        flow = update_nodes(flow, nodes)
        assert flow.version == 1
        assert flow.versions.count() == 1
        assert list(flow.nodes.values_list("pk", flat=True)) == pks


class TestCreateInstanceGuards:
    def test_missing_flow_returns_readable_error(self, applicant):
        """流程被删除（无实例可删）后发起：可读错误而不是 500。"""
        flow = publish("ver_missing_flow", [{"name": "A", "order": 1, **NODE_CORE}])
        ApprovalFlowNode.all_objects.filter(flow=flow).delete()
        flow.delete()
        instance, error = create_instance(flow=flow, applicant=applicant, title="孤儿单", form_data={})
        assert instance is None
        assert _gettext("The flow does not exist") in str(error)
