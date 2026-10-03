# -*- coding: utf-8 -*-
"""审批流程列表 form_schema_locked 预聚合：逐行 EXISTS 收敛为 Count(filter) 注解。

O11-3 抽样实测定位的 N+1（45 行列表 40 次逐行 ``bound_forms.filter().exists()``），
随 ``relation_count_fields`` 声明式口径预聚合；本文件守护该收敛不回退。
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from approval.models.approval import ApprovalFlow, ApprovalFlowNode
from dataset.models import DynamicForm
from system.models import UserInfo

pytestmark = pytest.mark.django_db

FLOWS_URL = "/api/approval/approval-flows"
FLOW_COUNT = 45


@pytest.fixture
def admin(db):
    return UserInfo.objects.create_superuser(
        username="flow_admin", email="flow_admin@example.com", password="Test@123456"
    )


@pytest.fixture
def flows(admin):
    """45 个流程（首个挂 3 节点）；前两个分别绑定「正式表单」与「模板表单」。"""
    rows = [
        ApprovalFlow(name=f"流程-{i}", code=f"prefetch_flow_{i}", form_schema=[], is_active=True, creator=admin)
        for i in range(FLOW_COUNT)
    ]
    ApprovalFlow.objects.bulk_create(rows)
    ApprovalFlowNode.objects.bulk_create(
        [ApprovalFlowNode(flow=rows[0], name=f"节点-{j}", order=j, condition={}) for j in range(1, 4)]
    )
    ApprovalFlowNode.objects.bulk_create(
        [ApprovalFlowNode(flow=rows[2], name="初审", order=1, condition={}) for _ in range(1)]
    )
    DynamicForm.objects.create(name="绑定正式表单", approval_flow=rows[0], is_template=False, creator=admin)
    DynamicForm.objects.create(name="绑定模板表单", approval_flow=rows[1], is_template=True, creator=admin)
    return rows


def _row_by_code(resp, code):
    return next(row for row in resp.data["data"]["results"] if row["code"] == code)


class TestFlowListFormLockPrefetch:
    def test_lock_semantics_with_bound_template_form(self, admin, flows, api_client):
        """绑定正式表单 → True；仅绑定模板表单 → False；未绑定 → False。"""
        api_client.force_authenticate(user=admin)
        resp = api_client.get(FLOWS_URL, {"size": FLOW_COUNT})
        assert resp.data["code"] == 1000
        by_code = {row["code"]: row for row in resp.data["data"]["results"]}
        assert by_code["prefetch_flow_0"]["form_schema_locked"] is True
        assert by_code["prefetch_flow_1"]["form_schema_locked"] is False
        assert by_code["prefetch_flow_2"]["form_schema_locked"] is False

    def test_list_no_per_row_dynamicform_queries(self, admin, flows, api_client):
        """列表的 dynamicform 访问只出现在主查询与分页 COUNT（注解 join），无逐行 EXISTS。"""
        api_client.force_authenticate(user=admin)
        api_client.get(FLOWS_URL, {"size": FLOW_COUNT})  # 预热（权限/配置查询）
        with CaptureQueriesContext(connection) as ctx:
            assert api_client.get(FLOWS_URL, {"size": FLOW_COUNT}).data["code"] == 1000
        dynamicform_queries = [q["sql"] for q in ctx.captured_queries if "dataset_dynamicform" in q["sql"]]
        # 上限 2 = 主查询 + 分页 COUNT，均整页一次；逐行 EXISTS 回退会随行数线性增加
        assert len(dynamicform_queries) <= 2, f"form_schema_locked 逐行 EXISTS 回退：{len(dynamicform_queries)} 条"

    def test_node_count_not_inflated_by_second_join(self, admin, flows, api_client):
        """同查询两处反向关联 join：node_count 必须 distinct，不被 bound_forms 行数放大。"""
        api_client.force_authenticate(user=admin)
        resp = api_client.get(FLOWS_URL, {"size": FLOW_COUNT})
        assert resp.data["code"] == 1000
        by_code = {row["code"]: row for row in resp.data["data"]["results"]}
        # 流程 0 绑定 1 张正式表单且挂 3 节点：交叉膨胀会得到 6，正确值是 3
        assert by_code["prefetch_flow_0"]["node_count"] == 3
        assert by_code["prefetch_flow_2"]["node_count"] == 1

    def test_single_object_fallback_keeps_exists(self, admin, flows):
        """脱离视图（无注解）直接序列化单个对象：回退单次 EXISTS，语义一致。"""
        from approval.serializers.approval_flow import ApprovalFlowSerializer

        data = ApprovalFlowSerializer(flows[0]).data
        assert data["form_schema_locked"] is True
        data = ApprovalFlowSerializer(flows[2]).data
        assert data["form_schema_locked"] is False
