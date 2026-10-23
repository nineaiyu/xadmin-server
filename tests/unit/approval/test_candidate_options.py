# -*- coding: utf-8 -*-
"""审批人候选目录：关键字远程搜索与截断信息。"""

import pytest

from approval.views.approval_rule import build_candidate_options
from identity.models import UserInfo

pytestmark = pytest.mark.django_db


def test_candidate_options_filters_users_by_keyword():
    UserInfo.objects.create_user(username="alice01", nickname="爱丽丝", password="Test@123456")
    UserInfo.objects.create_user(username="bob02", nickname="鲍勃", password="Test@123456")

    data = build_candidate_options(keyword="alice", limit=10)

    assert [item["username"] for item in data["users"]] == ["alice01"]
    assert data["total_users"] == 1
    assert data["truncated"] is False


def test_candidate_options_reports_truncation():
    for index in range(3):
        UserInfo.objects.create_user(username=f"cand{index}", password="Test@123456")

    data = build_candidate_options(limit=1)

    assert len(data["users"]) == 1
    assert data["total_users"] >= 3
    assert data["truncated"] is True
