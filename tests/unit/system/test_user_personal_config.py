# -*- coding: utf-8 -*-
"""批量建用户参数的事务与冲突预检。

历史缺陷：循环逐个 create，任一用户已有同名 key 即 IntegrityError 500，
且前序用户已落库（部分写入）。修复后：预查冲突返回含用户名的可读错误，
事务保证「要么全部建成、要么一条不写」；并发竞态由 IntegrityError 捕获
转可读错误兜底。
"""

import pytest
from rest_framework.exceptions import ValidationError

from system.models import UserInfo, UserPersonalConfig
from system.serializers.config import UserPersonalConfigSerializer

pytestmark = pytest.mark.django_db

KEY = "ui.sidebar"


def _serializer_payload(users, key=KEY, value=None):
    return {
        "key": key,
        "value": value if value is not None else {"collapsed": False},
        "config_user": [user.pk for user in users],
    }


class TestBatchCreateUserPersonalConfig:
    def test_batch_create_multiple_users(self):
        """多用户批量建参：逐用户各落一条，返回最后一条实例。"""
        users = [UserInfo.objects.create_user(username=f"cfg_u{i}", password="Test@123456") for i in range(3)]
        serializer = UserPersonalConfigSerializer(data=_serializer_payload(users))
        assert serializer.is_valid(), serializer.errors
        instance = serializer.save()
        assert UserPersonalConfig.objects.filter(key=KEY).count() == 3
        assert {row.owner_id for row in UserPersonalConfig.objects.filter(key=KEY)} == {user.pk for user in users}
        assert instance.owner_id == users[-1].pk

    def test_duplicate_users_in_request_deduped(self):
        """同一用户重复提交只建一条（否则撞 (owner, key) 唯一约束）。"""
        user = UserInfo.objects.create_user(username="cfg_dup", password="Test@123456")
        serializer = UserPersonalConfigSerializer(data=_serializer_payload([user, user, user]))
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        assert UserPersonalConfig.objects.filter(key=KEY, owner=user).count() == 1

    def test_conflict_precheck_readable_and_atomic(self):
        """冲突预检：错误信息含冲突用户名；同批其他用户不落库（无部分写入）。"""
        existing_owner = UserInfo.objects.create_user(username="cfg_exists", password="Test@123456")
        UserPersonalConfig.objects.create(owner=existing_owner, key=KEY, value={"old": True})
        fresh = UserInfo.objects.create_user(username="cfg_fresh", password="Test@123456")

        serializer = UserPersonalConfigSerializer(data=_serializer_payload([existing_owner, fresh]))
        assert serializer.is_valid(), serializer.errors
        with pytest.raises(ValidationError) as excinfo:
            serializer.save()
        assert "cfg_exists" in str(excinfo.value.detail[0] if excinfo.value.detail else excinfo.value)
        # 冲突用户之外的同批用户也不写入（预检先于任何 create，事务兜底）
        assert UserPersonalConfig.objects.filter(key=KEY, owner=fresh).exists() is False

    def test_integrity_error_converted_to_readable_error(self, monkeypatch):
        """并发竞态兜底：绕过预检直接撞唯一约束时，IntegrityError 转可读错误且不落半批。"""
        user_a = UserInfo.objects.create_user(username="cfg_race_a", password="Test@123456")
        user_b = UserInfo.objects.create_user(username="cfg_race_b", password="Test@123456")
        UserPersonalConfig.objects.create(owner=user_a, key=KEY, value={"old": True})

        # 模拟预检与写入之间的竞态窗口：预检被跳过，写入撞 (owner, key) 唯一约束
        monkeypatch.setattr(UserPersonalConfigSerializer, "_check_conflicts", lambda self, users, key: None)
        serializer = UserPersonalConfigSerializer(data=_serializer_payload([user_a, user_b]))
        assert serializer.is_valid(), serializer.errors
        with pytest.raises(ValidationError) as excinfo:
            serializer.save()
        assert "already exists" in str(excinfo.value.detail[0] if excinfo.value.detail else excinfo.value)
        # 事务回滚：user_b 的记录未落库，user_a 的存量记录未被破坏
        assert UserPersonalConfig.objects.filter(key=KEY, owner=user_b).exists() is False
        assert UserPersonalConfig.objects.get(key=KEY, owner=user_a).value == {"old": True}
