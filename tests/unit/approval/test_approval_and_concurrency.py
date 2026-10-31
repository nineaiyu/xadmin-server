# -*- coding: utf-8 -*-
"""会签并发正确性：AND 级两名审批人同时通过，节点必须结算而不是永久 PENDING。

无行锁时两个事务各自只看到自己的动作行 → 都返回「等待其他人 (1/2)」；
提交后动作已齐但节点仍 PENDING，且两人都已被 can_act 排除（已处理者不可操作），
整单永久卡死。锁内计数后，后提交者看到全票并推动节点完成。
"""

import threading

import pytest
from django.db import connections
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.viewsets import ViewSet

from approval.models.approval import ApprovalRequest
from approval.models.approval_rule import ApprovalRequestStep, ApprovalRule, ApprovalRuleLevel
from approval.services.approval import approve_request
from common.core.approval import ApprovalRequired
from common.core.response import ApiResponse
from identity.models import UserInfo

pytestmark = pytest.mark.django_db(transaction=True)


class DummyViewSet(ViewSet):
    permission_classes = [AllowAny]

    @ApprovalRequired()
    def destroy(self, request, pk=None):
        return ApiResponse(data={"executed": True, "pk": pk})


def _submit(applicant):
    from common.core.config import SysConfig

    SysConfig.set_value("APPROVAL_REQUIRED_PATHS", [r"^/api/test/"])
    factory = APIRequestFactory()
    request = factory.delete("/api/test/1", {}, format="json")
    force_authenticate(request, user=applicant)
    response = DummyViewSet.as_view({"delete": "destroy"})(request, pk="1")
    assert response.status_code == 412, response.data
    return ApprovalRequest.objects.get(creator=applicant, status=ApprovalRequest.Status.PENDING)


def test_concurrent_and_approvals_settle_node(transactional_db):
    applicant = UserInfo.objects.create_user(username="and-cc-applicant", password="Test@123456")
    first, second = [
        UserInfo.objects.create_user(username=f"and-cc-{index}", password="Test@123456") for index in (1, 2)
    ]
    rule = ApprovalRule.objects.create(name="会签并发规则", path_patterns=[r"^/api/test/"], priority=0)
    ApprovalRuleLevel.objects.create(
        rule=rule,
        name="会签级",
        order=1,
        approve_type="AND",
        assignee_type=ApprovalRuleLevel.AssigneeType.USER,
        assignee_value=f"{first.username},{second.username}",
    )
    record = _submit(applicant)
    step = record.steps.get(order=1)
    assert step.approve_type == "AND"

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def worker(user_pk):
        try:
            barrier.wait(timeout=10)
            user = UserInfo.objects.get(pk=user_pk)
            approval = ApprovalRequest.objects.get(pk=record.pk)
            results.append(approve_request(approval, user)[0])
        except Exception as exc:  # noqa: BLE001 线程异常汇总后断言
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(user.pk,)) for user in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert results == [True, True]
    record.refresh_from_db()
    step.refresh_from_db()
    assert step.actions.count() == 2
    assert step.status == ApprovalRequestStep.Status.APPROVED
    # 单级结构：全票后整单通过（无锁实现会停在 PENDING）
    assert record.status == ApprovalRequest.Status.APPROVED
