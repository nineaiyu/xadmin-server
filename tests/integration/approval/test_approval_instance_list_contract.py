# -*- coding: utf-8 -*-
"""审批实例列表契约：列表只含表格列 + 行内按钮字段，详情专属重字段不进列表。

守护两件事：
1. 字段面：tasks / form_schema / related_object / node_progress / comments /
   cc_users / reason / biz_type / biz_id 仅在 retrieve 返回；表格列（table_fields）
   与行内按钮依赖的 my_task / form_data / tags 保留；
2. 查询面：列表查询数不随「关联业务对象」行数增长（related_object 不在列表计算，
   历史实现逐行查业务表）。
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from approval.models.approval import ApprovalFlow, ApprovalFlowNode
from approval.models.leave import Leave
from approval.utils.approval_flow import create_instance
from identity.models import UserInfo

pytestmark = pytest.mark.django_db

INSTANCES_URL = "/api/approval/approval-instances"

# 详情专属字段（列表不返回）
DETAIL_ONLY_FIELDS = (
    "tasks",
    "form_schema",
    "related_object",
    "node_progress",
    "comments",
    "cc_users",
    "reason",
    "biz_type",
    "biz_id",
)
# 列表必须保留的字段：表格列 + 行内按钮（加签/转交吃 my_task，驳回重提预填吃 form_data）
LIST_REQUIRED_FIELDS = (
    "pk",
    "title",
    "flow_name",
    "status",
    "current_node_name",
    "current_assignees",
    "creator",
    "finished_at",
    "created_time",
    "my_task",
    "form_data",
    "tags",
)


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_superuser(
        username="list_contract_applicant", email="lc_applicant@example.com", password="Test@123456"
    )


@pytest.fixture
def approver(db):
    return UserInfo.objects.create_superuser(
        username="list_contract_approver", email="lc_approver@example.com", password="Test@123456"
    )


def make_flow(code: str) -> ApprovalFlow:
    flow = ApprovalFlow.objects.create(name=f"流程-{code}", code=code, form_schema=[], is_active=True)
    ApprovalFlowNode.objects.create(
        flow=flow,
        name="初审",
        order=1,
        approve_type=ApprovalFlowNode.ApproveType.OR,
        assignee_type=ApprovalFlowNode.AssigneeType.USER,
        assignee_value="list_contract_approver",
        condition={},
        timeout_hours=0,
    )
    return flow


def submit(flow, applicant, title, biz_type="", biz_id=""):
    instance, error = create_instance(
        flow=flow, applicant=applicant, title=title, form_data={}, biz_type=biz_type, biz_id=biz_id
    )
    assert error is None, error
    return instance


class TestInstanceListFieldContract:
    def test_list_excludes_detail_only_fields(self, applicant, approver, api_client):
        flow = make_flow("list_contract_fields")
        submit(flow, applicant, "列表字段契约")
        api_client.force_authenticate(user=applicant)

        resp = api_client.get(INSTANCES_URL, {"scope": "mine"})
        assert resp.data["code"] == 1000
        rows = resp.data["data"]["results"]
        assert len(rows) == 1
        row = rows[0]
        for field in DETAIL_ONLY_FIELDS:
            assert field not in row, f"列表不应返回详情专属字段 {field}"
        for field in LIST_REQUIRED_FIELDS:
            assert field in row, f"列表缺少必需字段 {field}"

    def test_detail_keeps_detail_only_fields(self, applicant, approver, api_client):
        flow = make_flow("list_contract_detail")
        instance = submit(flow, applicant, "详情字段契约")
        api_client.force_authenticate(user=applicant)

        resp = api_client.get(f"{INSTANCES_URL}/{instance.pk}")
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        for field in DETAIL_ONLY_FIELDS:
            assert field in data, f"详情缺少字段 {field}"

    def test_list_query_count_independent_of_related_biz_rows(self, applicant, approver, api_client):
        """3 条实例 → 追加 3 条带业务关联实例：查询数不随行数增长。"""
        flow = make_flow("list_contract_queries")
        for index in range(3):
            submit(flow, applicant, f"无关联-{index}")
        api_client.force_authenticate(user=applicant)
        # 预热：首请求的冷启动配置/权限查询不计入差值（两次采样都在预热之后）
        api_client.get(INSTANCES_URL, {"page": 1, "size": 50})

        with CaptureQueriesContext(connection) as base:
            resp_base = api_client.get(INSTANCES_URL, {"page": 1, "size": 50})
        assert resp_base.data["data"]["total"] == 3

        for index in range(3):
            leave = Leave.objects.create(
                creator=applicant,
                leave_type=Leave.LeaveType.ANNUAL,
                start_date="2026-10-01",
                end_date="2026-10-02",
                days=2,
                reason=f"列表查询数守护-{index}",
            )
            submit(flow, applicant, f"有关联-{index}", biz_type="leave", biz_id=str(leave.pk))

        with CaptureQueriesContext(connection) as with_biz:
            resp_biz = api_client.get(INSTANCES_URL, {"page": 1, "size": 50})
        assert resp_biz.data["data"]["total"] == 6

        # 行数翻倍但查询数不增长（容差 1：分页/序列化外壳的常数差异）
        assert len(with_biz.captured_queries) <= len(base.captured_queries) + 1

    def test_related_object_not_computed_in_list(self, applicant, approver, api_client, monkeypatch):
        """列表零业务表查询：即使存在关联实例，也不调用 biz_summary；详情仍调用。"""
        flow = make_flow("list_contract_biz_call")
        leave = Leave.objects.create(
            creator=applicant,
            leave_type=Leave.LeaveType.SICK,
            start_date="2026-10-05",
            end_date="2026-10-06",
            days=2,
            reason="不应被列表查询",
        )
        instance = submit(flow, applicant, "有关联实例", biz_type="leave", biz_id=str(leave.pk))
        api_client.force_authenticate(user=applicant)

        called = []

        def spy(obj):
            called.append(obj.pk)
            return None

        # 序列化器内部按函数级 import 取 biz_summary，patch 其来源模块属性
        monkeypatch.setattr("approval.utils.approval_flow.biz.biz_summary", spy)

        assert api_client.get(INSTANCES_URL, {"scope": "mine"}).data["code"] == 1000
        assert called == []
        assert api_client.get(f"{INSTANCES_URL}/{instance.pk}").data["code"] == 1000
        assert called, "详情应计算 related_object"
