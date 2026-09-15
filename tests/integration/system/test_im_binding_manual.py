# -*- coding: utf-8 -*-
"""管理员代录 IM 账号（免扫码绑定）集成测试。"""

import pytest

from system.models import OperationLog, UserInfo, UserOAuthBinding

pytestmark = pytest.mark.django_db

USERS_URL = "/api/system/user"


@pytest.fixture
def target_user(db):
    return UserInfo.objects.create_user(username="im_target", password="Test@123456")


class TestManualImBinding:
    def test_bind_and_list(self, auth_client, target_user):
        resp = auth_client.post(
            f"{USERS_URL}/{target_user.pk}/im-binding",
            {"provider": "dingtalk", "subject": "unionid-abc", "nickname": "钉钉账号"},
            format="json",
        )
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["created"] is True
        binding = UserOAuthBinding.objects.get(user=target_user, provider="dingtalk")
        assert binding.subject == "unionid-abc"
        assert binding.profile.get("source") == "admin_manual"
        # 审计
        assert OperationLog.objects.filter(module="IM:binding", object_pk=str(target_user.pk)).exists()

        listed = auth_client.get(f"{USERS_URL}/{target_user.pk}/im-binding")
        assert listed.data["code"] == 1000
        assert listed.data["data"][0]["subject"] == "unionid-abc"

    def test_update_existing_binding(self, auth_client, target_user):
        auth_client.post(
            f"{USERS_URL}/{target_user.pk}/im-binding",
            {"provider": "dingtalk", "subject": "unionid-abc"},
            format="json",
        )
        resp = auth_client.post(
            f"{USERS_URL}/{target_user.pk}/im-binding",
            {"provider": "dingtalk", "subject": "unionid-xyz"},
            format="json",
        )
        assert resp.data["code"] == 1000
        assert resp.data["data"]["created"] is False
        assert UserOAuthBinding.objects.get(user=target_user).subject == "unionid-xyz"

    def test_subject_conflict_rejected(self, auth_client, target_user):
        other = UserInfo.objects.create_user(username="im_other", password="Test@123456")
        UserOAuthBinding.objects.create(user=other, provider="dingtalk", subject="unionid-abc")
        resp = auth_client.post(
            f"{USERS_URL}/{target_user.pk}/im-binding",
            {"provider": "dingtalk", "subject": "unionid-abc"},
            format="json",
        )
        assert resp.data["code"] == 1001, resp.data

    def test_missing_fields_rejected(self, auth_client, target_user):
        resp = auth_client.post(f"{USERS_URL}/{target_user.pk}/im-binding", {"provider": "dingtalk"}, format="json")
        assert resp.data["code"] == 1001, resp.data

    def test_unbind(self, auth_client, target_user):
        UserOAuthBinding.objects.create(user=target_user, provider="dingtalk", subject="unionid-abc")
        resp = auth_client.post(f"{USERS_URL}/{target_user.pk}/im-unbind", {"provider": "dingtalk"}, format="json")
        assert resp.data["code"] == 1000, resp.data
        assert not UserOAuthBinding.objects.filter(user=target_user).exists()

    def test_unbind_last_login_method_rejected(self, auth_client, db):
        """防自锁：无可用密码且仅剩此绑定的账号拒绝解绑。"""
        user = UserInfo.objects.create_user(username="im_nopwd", password=None)
        user.set_unusable_password()
        user.save(update_fields=["password"])
        UserOAuthBinding.objects.create(user=user, provider="dingtalk", subject="unionid-last")
        resp = auth_client.post(f"{USERS_URL}/{user.pk}/im-unbind", {"provider": "dingtalk"}, format="json")
        assert resp.data["code"] == 1001, resp.data
        assert UserOAuthBinding.objects.filter(user=user).exists()
