# -*- coding: utf-8 -*-
"""审批实例列表任务预取收敛：列表/导出只取当前待办，历史任务不进内存（详情保持全量）。"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from approval.models.approval import ApprovalFlow, ApprovalFlowNode, ApprovalNodeTask
from approval.utils.approval_flow import approve_task, create_instance
from identity.models import UserInfo

pytestmark = pytest.mark.django_db

INSTANCES_URL = "/api/approval/approval-instances"


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_superuser(
        username="prefetch_applicant", email="prefetch_applicant@example.com", password="Test@123456"
    )


@pytest.fixture
def first_approver(db):
    return UserInfo.objects.create_user(username="prefetch_first", password="Test@123456", nickname="初审人")


@pytest.fixture
def second_approver(db):
    return UserInfo.objects.create_user(username="prefetch_second", password="Test@123456", nickname="复审人")


def make_two_node_flow(code, first, second):
    flow = ApprovalFlow.objects.create(name=f"流程-{code}", code=code, form_schema=[], is_active=True)
    for order, name, user in ((1, "初审", first), (2, "复审", second)):
        ApprovalFlowNode.objects.create(
            flow=flow,
            name=name,
            order=order,
            approve_type=ApprovalFlowNode.ApproveType.OR,
            assignee_type=ApprovalFlowNode.AssigneeType.USER,
            assignee_value=user.username,
            condition={},
            timeout_hours=0,
        )
    return flow


def advance_to_second_node(flow, applicant, first_approver):
    """发起并推进到第二节点：产生 1 条历史任务（初审已办）+ 1 条当前待办（复审）。"""
    instance, error = create_instance(flow=flow, applicant=applicant, title="两节点申请", form_data={})
    assert error is None, error
    task = instance.tasks.get(status=ApprovalNodeTask.Status.PENDING)
    ok, detail = approve_task(task.pk, first_approver, "同意")
    assert ok, detail
    instance.refresh_from_db()
    assert instance.tasks.count() == 2
    return instance


def task_queries(ctx):
    return [q["sql"] for q in ctx.captured_queries if "approval_approvalnodetask" in q["sql"]]


class TestListTaskPrefetchScope:
    def test_list_renders_current_assignees_from_pending_tasks(
        self, applicant, first_approver, second_approver, api_client
    ):
        flow = make_two_node_flow("prefetch_list", first_approver, second_approver)
        advance_to_second_node(flow, applicant, first_approver)
        api_client.force_authenticate(user=applicant)

        resp = api_client.get(INSTANCES_URL, {"scope": "mine"})
        assert resp.data["code"] == 1000
        row = resp.data["data"]["results"][0]
        # 当前待办人是复审人；历史初审人不出现在 current_assignees
        assert row["current_assignees"] == "复审人"
        assert "初审人" not in row["current_assignees"]

    def test_list_task_prefetch_filters_pending(self, applicant, first_approver, second_approver, api_client):
        """列表的任务预取只有一条查询且带 status 过滤（历史任务不进入内存）。"""
        flow = make_two_node_flow("prefetch_sql", first_approver, second_approver)
        advance_to_second_node(flow, applicant, first_approver)
        api_client.force_authenticate(user=applicant)
        api_client.get(INSTANCES_URL, {"scope": "mine"})  # 预热（权限/配置查询）

        with CaptureQueriesContext(connection) as ctx:
            assert api_client.get(INSTANCES_URL, {"scope": "mine"}).data["code"] == 1000

        queries = task_queries(ctx)
        assert len(queries) == 1, f"列表任务预取应只有一条查询，实际 {len(queries)}"
        assert '"status"' in queries[0], "预取 SQL 必须带 status 过滤（只取当前待办）"

    def test_list_query_count_independent_of_history_length(
        self, applicant, first_approver, second_approver, api_client
    ):
        """追加历史任务行不增加列表查询数（预取一次性，行数不影响查询数）。"""
        flow = make_two_node_flow("prefetch_queries", first_approver, second_approver)
        instance = advance_to_second_node(flow, applicant, first_approver)
        api_client.force_authenticate(user=applicant)
        api_client.get(INSTANCES_URL, {"scope": "mine"})

        with CaptureQueriesContext(connection) as base:
            api_client.get(INSTANCES_URL, {"scope": "mine"})

        first_node = flow.nodes.order_by("order").first()
        ApprovalNodeTask.objects.bulk_create(
            [
                ApprovalNodeTask(
                    instance=instance,
                    node=first_node,
                    node_name="历史节点",
                    node_order=1,
                    assignee=first_approver,
                    status=ApprovalNodeTask.Status.APPROVED,
                )
                for _ in range(20)
            ]
        )

        with CaptureQueriesContext(connection) as after:
            resp = api_client.get(INSTANCES_URL, {"scope": "mine"})
        assert resp.data["code"] == 1000
        assert len(after.captured_queries) <= len(base.captured_queries) + 1

    def test_detail_keeps_full_task_timeline(self, applicant, first_approver, second_approver, api_client):
        """详情不受预取收敛影响：仍返回全量审批轨迹（时间线渲染）。"""
        flow = make_two_node_flow("prefetch_detail", first_approver, second_approver)
        instance = advance_to_second_node(flow, applicant, first_approver)
        api_client.force_authenticate(user=applicant)

        resp = api_client.get(f"{INSTANCES_URL}/{instance.pk}")
        assert resp.data["code"] == 1000
        tasks = resp.data["data"]["tasks"]
        assert len(tasks) == 2
        assert {task["status"]["value"] for task in tasks} == {"APPROVED", "PENDING"}

    def test_export_task_prefetch_filters_pending(self, applicant, first_approver, second_approver, api_client):
        """导出链路与列表同口径：任务预取同样只取当前待办。"""
        flow = make_two_node_flow("prefetch_export", first_approver, second_approver)
        advance_to_second_node(flow, applicant, first_approver)
        api_client.force_authenticate(user=applicant)
        api_client.get(INSTANCES_URL, {"scope": "mine"})

        with CaptureQueriesContext(connection) as ctx:
            resp = api_client.get(f"{INSTANCES_URL}/export-data", {"scope": "mine", "type": "csv"})
        assert resp.status_code == 200

        queries = task_queries(ctx)
        assert queries, "导出应有任务预取查询"
        assert all('"status"' in sql for sql in queries), "导出预取必须带 status 过滤"
