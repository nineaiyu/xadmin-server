# -*- coding: utf-8 -*-
"""审批通过后自动执行：handler 半途失败必须回滚，重放不产生重复副作用。"""

import pytest

from approval.models.approval_request import ApprovalRequest
from approval.services.approval.approved_actions import (
    ON_APPROVED_HANDLERS,
    register_on_approved,
    run_on_approved,
)
from identity.models import UserInfo

pytestmark = pytest.mark.django_db

PATH = "/api/test/rel-appr-p2"
SIDE_EFFECT_USERNAME = "rel_appr_p2_side_effect"


@pytest.fixture
def approval(superuser):
    return ApprovalRequest.objects.create(
        module="自动执行回归",
        method="POST",
        path=PATH,
        creator=superuser,
        params={},
        payload={"value": 1},
    )


@pytest.fixture(autouse=True)
def _clean_handler():
    yield
    ON_APPROVED_HANDLERS.pop(PATH, None)


class TestAutoExecuteAtomic:
    def test_handler_exception_rolls_back_and_retry_runs_once(self, approval, superuser):
        calls = []

        def flaky(_approval, _user):
            calls.append(1)
            UserInfo.objects.create_user(username=SIDE_EFFECT_USERNAME, password="Test@123456")
            if len(calls) == 1:
                raise RuntimeError("half-way failure")
            return True, None

        register_on_approved(PATH, flaky)
        run_on_approved(approval, superuser)

        approval.refresh_from_db()
        assert approval.auto_completed is False
        assert approval.consume_time is None
        assert not UserInfo.objects.filter(username=SIDE_EFFECT_USERNAME).exists()

        # 手动/自动重放：上一轮副作用已回滚，重跑只建一行并标记完成
        run_on_approved(approval, superuser)
        assert UserInfo.objects.filter(username=SIDE_EFFECT_USERNAME).count() == 1
        approval.refresh_from_db()
        assert approval.auto_completed is True

        # 完成后再次触发不重复执行
        run_on_approved(approval, superuser)
        assert UserInfo.objects.filter(username=SIDE_EFFECT_USERNAME).count() == 1
        assert len(calls) == 2
