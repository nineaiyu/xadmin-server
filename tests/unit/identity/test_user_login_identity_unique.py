# -*- coding: utf-8 -*-
"""登录标识（email/phone）在活跃数据内的数据库级唯一性。"""

import pytest
from django.db import IntegrityError, transaction

from identity.models import UserInfo

pytestmark = pytest.mark.django_db


def _create(username, **kwargs):
    return UserInfo.objects.create_user(username=username, password="Test@123456", **kwargs)


class TestLoginIdentityUnique:
    def test_duplicate_active_email_rejected(self):
        _create("uniq_mail_a", email="dup@example.com")
        with pytest.raises(IntegrityError), transaction.atomic():
            _create("uniq_mail_b", email="dup@example.com")

    def test_duplicate_active_phone_rejected(self):
        _create("uniq_phone_a", phone="13800000001")
        with pytest.raises(IntegrityError), transaction.atomic():
            _create("uniq_phone_b", phone="13800000001")

    def test_blank_emails_and_phones_are_not_unique(self):
        _create("uniq_blank_a")
        _create("uniq_blank_b")
        assert UserInfo.objects.filter(email="").count() == 2

    def test_soft_deleted_value_can_be_reused(self):
        first = _create("uniq_deleted", email="reuse@example.com")
        first.delete()
        second = _create("uniq_reused", email="reuse@example.com")
        assert second.email == "reuse@example.com"
        assert UserInfo.all_objects.filter(email="reuse@example.com").count() == 2
