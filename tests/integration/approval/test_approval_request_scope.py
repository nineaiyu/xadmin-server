# -*- coding: utf-8 -*-
"""审批中心（敏感操作审批单）待办页签的筛选与排序。

scope=pending 是取值域收窄，不是重建 queryset：DjangoFilterBackend 的筛选、
OrderingFilter 的排序与 get_queryset 的取值域都必须保留，否则管理员看到的
「已筛选」列表实际是完整待办集。
"""

import datetime

import pytest
from django.utils import timezone

from approval.models.approval import ApprovalRequest
from approval.services.approval import pending_count_for
from identity.models import UserInfo

pytestmark = pytest.mark.django_db

APPROVALS_URL = "/api/approval/approvals"


@pytest.fixture
def applicant(db):
    return UserInfo.objects.create_superuser(
        username="scope_applicant", email="scope_applicant@example.com", password="Test@123456"
    )


@pytest.fixture
def approver(db):
    return UserInfo.objects.create_superuser(
        username="scope_approver", email="scope_approver@example.com", password="Test@123456"
    )


def make_request(creator, module):
    return ApprovalRequest.objects.create(
        module=module,
        method="DELETE",
        path="/api/test/1",
        status=ApprovalRequest.Status.PENDING,
        creator=creator,
        params={},
        payload={},
    )


class TestPendingScopeKeepsQuery:
    def test_pending_scope_keeps_module_filter(self, api_client, applicant, approver):
        alpha = make_request(applicant, "alpha")
        make_request(applicant, "beta")

        api_client.force_authenticate(user=approver)
        resp = api_client.get(APPROVALS_URL, {"scope": "pending", "module": "alpha"})
        assert resp.data["code"] == 1000
        rows = resp.data["data"]["results"]
        assert [row["pk"] for row in rows] == [str(alpha.pk)]

    def test_pending_scope_keeps_ordering(self, api_client, applicant, approver):
        earlier = make_request(applicant, "alpha")
        later = make_request(applicant, "alpha")
        ApprovalRequest.objects.filter(pk=earlier.pk).update(created_time=timezone.now() - datetime.timedelta(days=1))

        api_client.force_authenticate(user=approver)
        resp = api_client.get(APPROVALS_URL, {"scope": "pending", "ordering": "created_time"})
        assert resp.data["code"] == 1000
        pks = [row["pk"] for row in resp.data["data"]["results"]]
        assert pks == [str(earlier.pk), str(later.pk)]

    def test_pending_scope_count_matches_badge(self, api_client, applicant, approver):
        make_request(applicant, "alpha")
        make_request(applicant, "beta")

        api_client.force_authenticate(user=approver)
        resp = api_client.get(APPROVALS_URL, {"scope": "pending"})
        assert resp.data["data"]["total"] == pending_count_for(approver)
