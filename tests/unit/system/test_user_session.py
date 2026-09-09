# -*- coding: utf-8 -*-
"""用户会话管理：登录登记、sid 精确失效、在线列表统一数据源、过期与清理。

覆盖第一期登记的「纯 HTTP 会话不进在线列表」边界：
- 登录/WS 接入登记 UserSession，token 携带 sid claim（refresh 派生/轮换继承）；
- 在线列表 = WS 会话（channel 存活）∪ HTTP 会话（last_active 活跃窗口内）；
- 行维度下线按 sid 服务端失效，不影响同用户其他登录；
- 强制下线/登出/过期任务置离线，保留期任务回收历史记录。
"""

from datetime import timedelta

import pytest
import time
from django.core.cache import cache
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from common.cache.storage import SessionTokenRevokedCache
from common.core.auth import ServerAccessToken
from message import utils as msg_utils
from system.models import UserSession
from system.models.log import UserLoginLog
from system.utils.session import (
    bind_session_claim,
    clean_expired_sessions,
    expire_stale_sessions,
    force_logout_user,
    register_user_session,
)
from system.views.admin.online import UserOnlineViewSet

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_online_snapshot():
    cache.delete(msg_utils.ONLINE_INFO_CACHE_KEY)
    yield
    cache.delete(msg_utils.ONLINE_INFO_CACHE_KEY)


@pytest.fixture
def layer(settings):
    """测试用内存 channel layer（tests/channel_layer.py 提供同名方法）。"""
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    layer._online_users = {}
    if hasattr(layer, "groups") and hasattr(layer.groups, "clear"):
        layer.groups.clear()
    yield layer
    layer._online_users = {}
    if hasattr(layer, "groups") and hasattr(layer.groups, "clear"):
        layer.groups.clear()


def _beat(layer, user_pk, channel="chan"):
    group = msg_utils.get_user_layer_group_name(user_pk)
    # groups 值为 {channel: score} dict（get_layers_for_groups 按 .keys() 取 channel）
    layer.groups.setdefault(group, {})[channel] = 0
    layer._online_users[user_pk] = time.time()


@pytest.fixture
def login_free(settings):
    """关闭登录辅助安全项：验证码 / 加密 / 临时 token（同 test_auth_api.py）。"""
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


def _access_from_refresh(refresh_str):
    return str(RefreshToken(refresh_str).access_token)


class TestSessionRegistration:
    def test_register_http_session(self, superuser):
        session = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        assert session.pk
        assert session.status == UserSession.Status.ONLINE
        assert session.channel_name == ""  # 纯 HTTP 会话无 channel
        assert session.creator == superuser

    def test_register_ws_session_keeps_channel(self, superuser):
        session = register_user_session(
            None, superuser, UserLoginLog.LoginTypeChoices.WEBSOCKET, channel_name="chan-ws"
        )
        assert session.channel_name == "chan-ws"


class TestSessionClaim:
    def test_basic_login_registers_session_and_claim(self, normal_user, login_free):
        """账密登录全链路：会话登记 + token 携带 sid。"""
        from rest_framework.test import APIClient

        client = APIClient()
        resp = client.post(
            "/api/system/login/basic",
            {"username": normal_user.username, "password": "Test@123456"},
            format="json",
        )
        assert resp.status_code == 200, resp.data
        access = resp.data["data"]["access"]
        assert UserSession.objects.filter(creator=normal_user, status=UserSession.Status.ONLINE).exists()
        assert "sid" in ServerAccessToken(access).payload

    def test_session_revoke_rejects_token(self, superuser):
        """单会话下线：sid 命中即拒绝，且 refresh 派生的新 access 同样失效。"""
        session = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        refresh = RefreshToken.for_user(superuser)
        refresh_str, access_str = bind_session_claim(refresh, session.pk)

        ServerAccessToken(access_str.encode()).verify()

        SessionTokenRevokedCache(session.pk).set_storage_cache(1)
        with pytest.raises(TokenError):
            ServerAccessToken(access_str.encode()).verify()
        # refresh 轮换/续命后派生的新 access 继承 sid，一并失效
        with pytest.raises(TokenError):
            ServerAccessToken(_access_from_refresh(refresh_str).encode())

    def test_other_sessions_unaffected(self, superuser):
        """sid 失效是会话级：同用户其他登录不受行维度下线影响。"""
        kept = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        kicked = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        refresh = RefreshToken.for_user(superuser)
        _, kept_access = bind_session_claim(refresh, kept.pk)

        SessionTokenRevokedCache(kicked.pk).set_storage_cache(1)

        ServerAccessToken(kept_access.encode()).verify()  # 不抛错即可

    def test_legacy_token_without_sid_passes(self, superuser):
        """历史 token（无 sid claim）不受会话级失效影响，向后兼容。"""
        access = str(ServerAccessToken.for_user(superuser))
        ServerAccessToken(access.encode()).verify()


class TestOnlineList:
    def test_http_session_listed_while_active(self, layer, superuser):
        session = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        assert session in list(UserOnlineViewSet().get_queryset())

    def test_http_session_dropped_when_stale(self, layer, superuser):
        session = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        UserSession.objects.filter(pk=session.pk).update(last_active=timezone.now() - timedelta(seconds=3600))
        cache.delete(msg_utils.ONLINE_INFO_CACHE_KEY)
        assert session not in list(UserOnlineViewSet().get_queryset())

    def test_ws_session_listed_by_channel_liveness(self, layer, superuser):
        """WS 会话在线判定走 channel 存活（同旧口径），不依赖 last_active。"""
        session = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.WEBSOCKET, channel_name="c-ws")
        UserSession.objects.filter(pk=session.pk).update(
            last_active=timezone.now() - timedelta(hours=1)  # 心跳续期由 Redis 层负责
        )
        cache.delete(msg_utils.ONLINE_INFO_CACHE_KEY)
        assert session not in list(UserOnlineViewSet().get_queryset())  # channel 未接入

        _beat(layer, superuser.pk, "c-ws")
        cache.delete(msg_utils.ONLINE_INFO_CACHE_KEY)
        assert session in list(UserOnlineViewSet().get_queryset())

    def test_offline_session_never_listed(self, layer, superuser):
        session = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        session.mark_offline()
        cache.delete(msg_utils.ONLINE_INFO_CACHE_KEY)
        assert session not in list(UserOnlineViewSet().get_queryset())


class TestSessionOffline:
    def test_perform_destroy_revokes_http_session(self, superuser):
        """行维度「下线」HTTP 会话：sid 失效 + 置离线，行从列表消失。"""
        session = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        refresh = RefreshToken.for_user(superuser)
        _, access_str = bind_session_claim(refresh, session.pk)

        UserOnlineViewSet().perform_destroy(session)

        session.refresh_from_db()
        assert session.status == UserSession.Status.OFFLINE
        with pytest.raises(TokenError):
            ServerAccessToken(access_str.encode()).verify()

    def test_force_logout_marks_all_sessions_offline(self, superuser):
        register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.WEBSOCKET, channel_name="c-x")

        force_logout_user(superuser.pk)

        assert not UserSession.objects.filter(creator=superuser, status=UserSession.Status.ONLINE).exists()


class TestExpireAndClean:
    def test_expire_stale_http_sessions_only(self, superuser):
        fresh = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        stale = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        UserSession.objects.filter(pk=stale.pk).update(last_active=timezone.now() - timedelta(seconds=3600))

        count = expire_stale_sessions()

        fresh.refresh_from_db()
        stale.refresh_from_db()
        assert count == 1
        assert fresh.status == UserSession.Status.ONLINE
        assert stale.status == UserSession.Status.OFFLINE

    def test_clean_expired_sessions(self, superuser):
        old = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        UserSession.objects.filter(pk=old.pk).update(last_active=timezone.now() - timedelta(days=60))
        recent = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)

        removed = clean_expired_sessions()

        assert removed == 1
        assert not UserSession.objects.filter(pk=old.pk).exists()
        assert UserSession.objects.filter(pk=recent.pk).exists()

    def test_touch_throttled(self, superuser):
        session = register_user_session(None, superuser, UserLoginLog.LoginTypeChoices.USERNAME)
        assert UserSession.touch(session.pk) is True
        assert UserSession.touch(session.pk) is False  # 60s 门控窗口内不再写库
        cache.delete(f"session_touch_{session.pk}")
        assert UserSession.touch(session.pk) is True
