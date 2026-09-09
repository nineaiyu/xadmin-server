# -*- coding:utf-8 -*-
"""新设备/新 IP/新城市登录提醒：四元组维度判定 + 基线窗口 + 24h 节流 + 开关短路 + WS 边界。"""

import datetime
from unittest import mock

import pytest
from django.conf import settings
from django.test import RequestFactory
from django.utils import timezone

from system.models.log import UserLoginLog
from system.models.user import UserInfo
from system.utils.login_alert import maybe_alert_abnormal_login

pytestmark = pytest.mark.django_db


@pytest.fixture
def alert_enabled(monkeypatch):
    monkeypatch.setattr(settings, "SECURITY_ABNORMAL_LOGIN_ALERT_ENABLED", True)


def _login_history(user, **overrides):
    defaults = {
        "ipaddress": "10.0.0.1",
        "city": "局域网",
        "browser": "Chrome",
        "system": "macOS",
        "status": True,
        "login_type": UserLoginLog.LoginTypeChoices.USERNAME,
    }
    defaults.update(overrides)
    return UserLoginLog.objects.create(creator=user, **defaults)


def test_disabled_by_default_no_alert(superuser):
    """开关关闭（默认）：直接短路，无提醒。"""
    _login_history(superuser)
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async") as publish:
        maybe_alert_abnormal_login(superuser, "8.8.8.8", "洛杉矶", "Firefox", "Windows")
    publish.assert_not_called()


def test_first_login_no_alert(superuser, alert_enabled):
    """基线窗口内无历史（注册即首登）：不提醒，避免新用户骚扰。"""
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async") as publish:
        maybe_alert_abnormal_login(superuser, "8.8.8.8", "洛杉矶", "Firefox", "Windows")
    publish.assert_not_called()


def test_new_ip_and_device_and_city_alert(superuser, alert_enabled):
    """基线窗口内已有历史：新 IP + 新设备 + 新城市全部命中，提醒包含三维度。"""
    _login_history(superuser)
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async", autospec=True) as publish:
        maybe_alert_abnormal_login(superuser, "8.8.8.8", "洛杉矶", "Firefox", "Windows")
    publish.assert_called_once()
    message = publish.call_args.args[0]  # autospec: args[0] 为消息实例
    assert set(message.dimensions) == {"ip", "device", "city"}


def test_new_city_only(superuser, alert_enabled):
    """同 IP 同设备换了城市（公网）：仅命中 city 维度。"""
    _login_history(superuser, ipaddress="8.8.8.8", city="北京")
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async", autospec=True) as publish:
        maybe_alert_abnormal_login(superuser, "8.8.8.8", "上海", "Chrome", "macOS")
    publish.assert_called_once()
    assert publish.call_args.args[0].dimensions == ["city"]


def test_known_city_no_alert(superuser, alert_enabled):
    """IP/设备/城市均在历史中：不提醒。"""
    _login_history(superuser, ipaddress="8.8.8.8", city="北京")
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async") as publish:
        maybe_alert_abnormal_login(superuser, "8.8.8.8", "北京", "Chrome", "macOS")
    publish.assert_not_called()


def test_private_ip_skips_city_dimension(superuser, alert_enabled):
    """内网登录不参与城市判定（无城市语义，与异地城市提醒口径一致）。"""
    # 历史来自同一内网 IP（城市为公网归属地语义上的任意值），IP/设备均已知
    _login_history(superuser, ipaddress="10.0.0.1", city="北京")
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async") as publish:
        maybe_alert_abnormal_login(superuser, "10.0.0.1", "局域网", "Chrome", "macOS")
    publish.assert_not_called()


def test_new_device_only_same_ip(superuser, alert_enabled):
    """同 IP 换设备：仅命中 device 维度。"""
    _login_history(superuser)
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async", autospec=True) as publish:
        maybe_alert_abnormal_login(superuser, "10.0.0.1", "局域网", "Safari", "iOS")
    publish.assert_called_once()
    message = publish.call_args.args[0]
    assert message.dimensions == ["device"]


def test_known_ip_and_device_no_alert(superuser, alert_enabled):
    """IP/设备均在历史中：不提醒。"""
    _login_history(superuser)
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async") as publish:
        maybe_alert_abnormal_login(superuser, "10.0.0.1", "局域网", "Chrome", "macOS")
    publish.assert_not_called()


def test_history_outside_baseline_window_ignored(superuser, alert_enabled):
    """历史在基线窗口之外：视为无基线，不提醒（新窗口重新累积）。"""
    history = _login_history(superuser)
    # created_time 为 auto_now_add，create() 传入不生效，须 update 回填过期时间
    UserLoginLog.objects.filter(pk=history.pk).update(
        created_time=timezone.now() - datetime.timedelta(days=settings.SECURITY_LOGIN_BASELINE_DAYS + 1)
    )
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async") as publish:
        maybe_alert_abnormal_login(superuser, "8.8.8.8", "洛杉矶", "Firefox", "Windows")
    publish.assert_not_called()


def test_failed_logins_not_in_baseline(superuser, alert_enabled):
    """基线只看成功登录：失败记录不构成已知 IP/设备（成功历史来自其他 IP/设备）。"""
    _login_history(superuser, ipaddress="7.7.7.7", browser="Firefox", system="Windows", city="北京")
    _login_history(superuser, status=False)  # 失败记录：10.0.0.1 + Chrome/macOS 不应进入基线
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async", autospec=True) as publish:
        maybe_alert_abnormal_login(superuser, "10.0.0.1", "局域网", "Chrome", "macOS")
    publish.assert_called_once()
    assert set(publish.call_args.args[0].dimensions) == {"ip", "device"}


def test_throttle_same_user_dimensions_once_per_day(superuser, alert_enabled):
    """同用户同维度组合 24h 内只提醒一次。"""
    _login_history(superuser)
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async") as publish:
        maybe_alert_abnormal_login(superuser, "8.8.8.8", "洛杉矶", "Firefox", "Windows")
        maybe_alert_abnormal_login(superuser, "9.9.9.9", "纽约", "Safari", "iOS")
    assert publish.call_count == 1
    # 其他用户不受该用户节流影响
    other = UserInfo.objects.create_user(username="alert-other", password="x")
    _login_history(other)
    with mock.patch("system.notifications.AbnormalLoginMessage.publish_async") as publish2:
        maybe_alert_abnormal_login(other, "8.8.8.8", "洛杉矶", "Firefox", "Windows")
    publish2.assert_called_once()


def test_exception_never_breaks_login(superuser, alert_enabled):
    """判定/发送链路任何异常全吞，绝不影响登录主流程。"""
    _login_history(superuser)
    with mock.patch("system.utils.login_alert._detect_new_dimensions", side_effect=RuntimeError("boom")):
        # 不抛异常即通过
        maybe_alert_abnormal_login(superuser, "8.8.8.8", "洛杉矶", "Firefox", "Windows")


def test_websocket_login_skips_alert(superuser, alert_enabled):
    """计划登记边界：WS 接入不触发异常登录提醒（页面伴随登录已提醒过）。"""
    from system.views.auth.login import login_success

    _login_history(superuser)  # 新 IP/设备/城市本应全部命中
    request = RequestFactory().post("/api/system/user/login", REMOTE_ADDR="8.8.8.8", HTTP_USER_AGENT="Mozilla/5.0")
    with mock.patch("system.utils.login_alert.maybe_alert_abnormal_login") as maybe_alert:
        login_success(request, superuser, login_type=UserLoginLog.LoginTypeChoices.WEBSOCKET)
    maybe_alert.assert_not_called()


def test_http_login_triggers_alert(superuser, alert_enabled):
    """HTTP 登录收敛点正常触发提醒（WS 边界的对照用例）。"""
    from system.views.auth.login import login_success

    _login_history(superuser)
    request = RequestFactory().post("/api/system/user/login", REMOTE_ADDR="8.8.8.8", HTTP_USER_AGENT="Mozilla/5.0")
    with mock.patch("system.utils.login_alert.maybe_alert_abnormal_login", autospec=True) as maybe_alert:
        login_success(request, superuser, login_type=UserLoginLog.LoginTypeChoices.USERNAME)
    maybe_alert.assert_called_once()
