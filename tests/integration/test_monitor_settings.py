# -*- coding: utf-8 -*-
"""资源告警阈值配置与告警发布自愈测试。

- SecurityMonitorViewSet：阈值读取/保存（settings/serializers/security.py）
- ServerPerformanceCheckUtil：阈值运行时从 settings 读取（后台改配置即时生效）
- ServerPerformanceMessage.publish：订阅收件人为空时自愈补齐活跃超管
  （存量库订阅创建早于超管初始化，收件人为空导致告警静默失效的回归）

注意：Setting 保存会经 pubsub 订阅者把值回写到进程级 django.conf.settings，
测试内一律用 override_settings 固定阈值，避免跨用例污染。
"""

import pytest
from django.core import mail
from django.test import override_settings

from common.models import Monitor
from notifications.models import SystemMsgSubscription
from settings.models import Setting

pytestmark = pytest.mark.django_db

MONITOR_URL = "/api/settings/monitor/auth"


class TestMonitorSettingsAPI:
    def test_retrieve_returns_defaults(self, auth_client):
        resp = auth_client.get(MONITOR_URL)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        data = resp.data["data"]
        assert data["SECURITY_MONITOR_DISK_USED_MAX"] == 80
        assert data["SECURITY_MONITOR_MEMORY_USED_MAX"] == 85
        assert data["SECURITY_MONITOR_CPU_PERCENT_MAX"] == 80
        assert data["SECURITY_MONITOR_CPU_LOAD_MAX"] == 5

    def test_partial_update_persists(self, auth_client):
        resp = auth_client.patch(MONITOR_URL, {"SECURITY_MONITOR_DISK_USED_MAX": 90})
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        assert resp.data["data"]["SECURITY_MONITOR_DISK_USED_MAX"] == 90

        setting = Setting.objects.filter(name="SECURITY_MONITOR_DISK_USED_MAX").first()
        assert setting is not None
        assert setting.cleaned_value == 90

        # 生产上由 pubsub 订阅者执行；测试内直接模拟 worker 侧回写
        setting.refresh_setting()
        resp = auth_client.get(MONITOR_URL)
        assert resp.data["data"]["SECURITY_MONITOR_DISK_USED_MAX"] == 90

    def test_partial_update_rejects_invalid_value(self, auth_client):
        resp = auth_client.patch(MONITOR_URL, {"SECURITY_MONITOR_DISK_USED_MAX": 101})
        assert resp.status_code == 400

    def test_search_columns_renders_fields(self, auth_client):
        """资源告警配置表单依赖 search-columns 元数据。"""
        resp = auth_client.get(f"{MONITOR_URL}/search-columns")
        assert resp.status_code == 200
        assert resp.data["code"] == 1000
        keys = {col["key"] for col in resp.data["data"]}
        assert {
            "SECURITY_MONITOR_DISK_USED_MAX",
            "SECURITY_MONITOR_MEMORY_USED_MAX",
            "SECURITY_MONITOR_CPU_PERCENT_MAX",
            "SECURITY_MONITOR_CPU_LOAD_MAX",
        } <= keys


class TestServerPerformanceCheck:
    @staticmethod
    def _seed_monitor(**values):
        defaults = {"cpu_load": 1.0, "cpu_percent": 10.0, "memory_used": 20.0, "disk_used": 30.0}
        defaults.update(values)
        return Monitor.objects.create(**defaults)

    def test_below_threshold_no_alarm(self, superuser):
        self._seed_monitor()
        from common.notifications import ServerPerformanceCheckUtil

        util = ServerPerformanceCheckUtil()
        util.check()
        assert util.terms_with_errors == []

    def test_thresholds_read_from_settings_at_check_time(self, superuser):
        """阈值必须每次检查时读取（后台改配置经 pubsub 回写 settings 即时生效）。"""
        self._seed_monitor(disk_used=81)
        from common.notifications import ServerPerformanceCheckUtil

        with override_settings(SECURITY_MONITOR_DISK_USED_MAX=80):
            util = ServerPerformanceCheckUtil()
            util.check()
            assert util.terms_with_errors  # 81 已超标

        with override_settings(SECURITY_MONITOR_DISK_USED_MAX=90):
            util = ServerPerformanceCheckUtil()
            util.check()
            assert util.terms_with_errors == []  # 阈值调高后不再告警

    @override_settings(SECURITY_MONITOR_DISK_USED_MAX=80, EMAIL_ENABLED=True)
    def test_publish_self_heals_empty_receivers(self, superuser):
        """存量缺陷：订阅创建早于超管初始化时收件人为空，publish 需自愈而非静默丢弃。"""
        subscription, _ = SystemMsgSubscription.objects.get_or_create(message_type="ServerPerformanceMessage")
        subscription.users.clear()
        subscription.receive_backends = ["email"]
        subscription.save()

        self._seed_monitor(disk_used=95)
        from common.notifications import ServerPerformanceMessage, ServerPerformanceCheckUtil

        util = ServerPerformanceCheckUtil()
        util.check()
        assert util.terms_with_errors

        ServerPerformanceMessage(util.terms_with_errors).publish()

        subscription.refresh_from_db()
        assert list(subscription.users.values_list("username", flat=True)) == [superuser.username]
        assert mail.outbox
        assert superuser.email in mail.outbox[0].recipients()
