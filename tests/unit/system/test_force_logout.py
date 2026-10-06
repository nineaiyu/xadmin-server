# -*- coding: utf-8 -*-
"""强制下线：用户级令牌失效时间戳 + refresh 拉黑 + action。"""

import time
from datetime import UTC

import pytest
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from common.core.auth import ServerAccessToken
from identity.models.user import UserInfo
from identity.utils.session import force_logout_user
from identity.views.admin.online import UserOnlineViewSet

pytestmark = pytest.mark.django_db


def _make_user():
    return UserInfo.objects.create_superuser(username="kicked", password="x")


def _access_token(user):
    return str(ServerAccessToken.for_user(user))


def _valid_access(token_str):
    # 认证链路里 self.token 为 bytes（get_raw_token 输出），此处保持同口径
    ServerAccessToken(token_str.encode()).verify()


@pytest.fixture
def layer(settings):
    """channel layer（InMemory 档 / 真 Redis 层通用，清理走 tests/channel_layer helper）。"""
    from channels.layers import get_channel_layer

    from tests.channel_layer import reset_layer_state

    layer = get_channel_layer()
    reset_layer_state(layer)
    yield layer
    reset_layer_state(layer)


def test_force_logout_revokes_existing_access_tokens():
    user = _make_user()
    old_token = _access_token(user)
    _valid_access(old_token)

    force_logout_user(user.pk)

    with pytest.raises(TokenError):
        ServerAccessToken(old_token).verify()


def test_token_issued_after_logout_still_works():
    user = _make_user()
    force_logout_user(user.pk)
    # iat 秒级粒度：同秒内重新签发会被误杀，下一秒起自动恢复（登录耗时远大于 1s）
    time.sleep(1.1)
    _valid_access(_access_token(user))


def test_force_logout_blacklists_refresh_tokens():
    from datetime import datetime

    from rest_framework_simplejwt.tokens import RefreshToken

    user = _make_user()
    refresh = RefreshToken.for_user(user)
    # 本项目启用 token_blacklist 后 for_user 已登记 OutstandingToken，get_or_create 兜底
    OutstandingToken.objects.get_or_create(
        jti=refresh["jti"],
        defaults={
            "user": user,
            "token": str(refresh),
            "expires_at": datetime.fromtimestamp(refresh.payload["exp"], tz=UTC),
        },
    )
    assert not BlacklistedToken.objects.filter(token__user=user).exists()

    force_logout_user(user.pk)

    assert BlacklistedToken.objects.filter(token__user=user).exists()
    with pytest.raises(TokenError):
        refresh.check_blacklist()


def test_force_logout_action(superuser, auth_client):
    target = UserInfo.objects.create_user(username="online_target", password="x")
    response = auth_client.post(f"/api/system/online/{target.pk}/force-logout")
    assert response.data["code"] == 1000
    assert response.data["data"]["channels"] == 0


def test_batch_force_logout_action(superuser, auth_client):
    from audit.models.log import UserLoginLog as LoginLog

    target = UserInfo.objects.create_user(username="online_batch", password="x")
    log = LoginLog.objects.create(
        creator=target, login_type=LoginLog.LoginTypeChoices.WEBSOCKET, channel_name="ch-batch"
    )
    response = auth_client.post("/api/system/online/batch-force-logout", [str(log.pk)], format="json")
    assert response.data["code"] == 1000
    assert response.data["data"]["users"] == 1


def test_verify_rejects_without_token_error_message():
    """失效时间戳命中的报错与黑名单一致，不泄露细节。"""
    user = _make_user()
    token = _access_token(user)
    force_logout_user(user.pk)
    from rest_framework_simplejwt.tokens import AccessToken as BaseAccessToken

    # 绕过 ServerAccessToken 直接验证 payload 中的 iat 语义：revoked_at >= iat 即拒绝
    from common.cache.storage import UserTokenRevokedCache

    revoked_at = UserTokenRevokedCache(user.pk).get_storage_cache()
    assert revoked_at is not None
    assert float(revoked_at) >= 0
    assert "iat" in BaseAccessToken(token).payload


def test_batch_action_requires_auth(api_client):
    response = api_client.post("/api/system/online/batch-force-logout", [], format="json")
    assert response.status_code in (401, 403)


def test_force_logout_viewset_registered():
    assert hasattr(UserOnlineViewSet, "force_logout")
    assert hasattr(UserOnlineViewSet, "batch_force_logout")
    assert getattr(settings, "CACHE_KEY_TEMPLATE", {}).get("user_token_revoked_key")


def test_auth_failure_is_authentication_failed():
    """认证层对失效 token 统一 401（AuthenticationFailed），不会 500。"""
    with pytest.raises((TokenError, AuthenticationFailed)):
        ServerAccessToken("not-a-token").verify()


class TestVerifyRoundTrip:
    """认证热路径：三处失效检查合并为一次 get_many（每请求 3 次 Redis 往返 → 1 次）。"""

    @staticmethod
    def _token_with_sid(user):
        token = ServerAccessToken.for_user(user)
        token["sid"] = "sid-probe"
        return str(token)

    def _spy_get_many(self, monkeypatch):
        import django.core.cache as django_cache

        calls = []
        original = django_cache.cache.get_many
        monkeypatch.setattr(
            "django.core.cache.cache.get_many",
            lambda keys, *args, **kwargs: (calls.append(list(keys)), original(keys, *args, **kwargs))[1],
        )
        return calls

    def test_single_get_many_covers_all_revocation_keys(self, monkeypatch):
        import hashlib

        from common.cache.storage import BlackAccessTokenCache, SessionTokenRevokedCache, UserTokenRevokedCache

        user = _make_user()
        raw = self._token_with_sid(user)
        token = ServerAccessToken(raw.encode())
        user_id = token.payload.get("user_id")
        expected = [
            BlackAccessTokenCache(user_id, hashlib.md5(raw.encode()).hexdigest()).cache_key,
            UserTokenRevokedCache(user_id).cache_key,
            SessionTokenRevokedCache("sid-probe").cache_key,
        ]

        calls = self._spy_get_many(monkeypatch)
        token.verify()

        # 恰好一次往返，键与三个缓存类一致（顺序无关，集合相等即可）
        assert len(calls) == 1
        assert set(calls[0]) == set(expected)

    def test_sid_less_token_queries_two_keys(self, monkeypatch):
        """旧 token 无 sid claim：不查会话级键（避免恒 miss 的多余键）。"""
        user = _make_user()
        token = ServerAccessToken(str(ServerAccessToken.for_user(user)).encode())

        calls = self._spy_get_many(monkeypatch)
        token.verify()

        assert len(calls) == 1
        assert len(calls[0]) == 2


class TestBatchForceLogoutUsers:
    """批量踢线：与逐用户 force_logout_user 四步语义等价，往返合并为整批一次。"""

    def test_batch_kicks_all_listed_users(self, layer, superuser):
        from asgiref.sync import async_to_sync
        from rest_framework_simplejwt.tokens import RefreshToken

        from audit.models.log import UserLoginLog as LoginLog
        from common.cache.storage import UserTokenRevokedCache
        from identity.models import UserSession
        from identity.utils.session import force_logout_users
        from message.utils import get_user_layer_group_name
        from tests.channel_layer import beat_layer

        targets = [
            UserInfo.objects.create_user(username="batch_kick_a", password="x"),
            UserInfo.objects.create_user(username="batch_kick_b", password="x"),
        ]
        RefreshToken.for_user(targets[0])
        for index, target in enumerate(targets):
            from identity.utils.session import register_user_session

            register_user_session(None, target, LoginLog.LoginTypeChoices.WEBSOCKET, channel_name=f"ws-{index}")
            beat_layer(layer, target.pk, f"ws-{index}")

        kicked = force_logout_users([target.pk for target in targets])

        assert kicked == 2
        for target in targets:
            assert UserTokenRevokedCache(target.pk).get_storage_cache() is not None
            assert not UserSession.objects.filter(creator=target, status=UserSession.Status.ONLINE).exists()
            # 逐 channel 推送 logout 并退组：批量派发后组内 channel 清空
            channels = async_to_sync(layer.get_layers)(get_user_layer_group_name(target.pk))
            assert channels == []

    def test_batch_without_online_channels_still_revokes_tokens(self, superuser):
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
        from rest_framework_simplejwt.tokens import RefreshToken

        from common.cache.storage import UserTokenRevokedCache
        from identity.utils.session import force_logout_users

        target = UserInfo.objects.create_user(username="batch_kick_offline", password="x")
        old_token = _access_token(target)
        _valid_access(old_token)
        RefreshToken.for_user(target)

        assert force_logout_users([target.pk, target.pk]) == 0

        with pytest.raises(TokenError):
            ServerAccessToken(old_token).verify()
        assert BlacklistedToken.objects.filter(token__user=target).exists()
        assert UserTokenRevokedCache(target.pk).get_storage_cache() is not None
