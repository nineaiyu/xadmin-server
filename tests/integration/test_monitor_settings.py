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

from notifications.models import SystemMsgSubscription
from settings.models import Setting
from system.models import Monitor

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
        from common.notifications import ServerPerformanceCheckUtil, ServerPerformanceMessage

        util = ServerPerformanceCheckUtil()
        util.check()
        assert util.terms_with_errors

        ServerPerformanceMessage(util.terms_with_errors).publish()

        subscription.refresh_from_db()
        assert list(subscription.users.values_list("username", flat=True)) == [superuser.username]
        assert mail.outbox
        assert superuser.email in mail.outbox[0].recipients()


class TestThresholdReconcile:
    """pubsub 丢消息时消费侧的阈值对账：一个收敛窗口内自动读到新阈值。"""

    @pytest.fixture(autouse=True)
    def _gate_and_runtime_restore(self):
        """清 TTL 闸门并保存/恢复运行时阈值（refresh_setting 改的是进程级 settings）。"""
        import django.conf
        from django.core.cache import cache

        from common.notifications import _THRESHOLD_RECONCILE_CACHE_KEY, MONITOR_THRESHOLD_SETTINGS

        cache.delete(_THRESHOLD_RECONCILE_CACHE_KEY)
        saved = {name: getattr(django.conf.settings, name, None) for name in MONITOR_THRESHOLD_SETTINGS}
        yield
        for name, value in saved.items():
            setattr(django.conf.settings, name, value)
        cache.delete(_THRESHOLD_RECONCILE_CACHE_KEY)

    @staticmethod
    def _persist_without_broadcast(name, value):
        """模拟另一进程直接改库且本进程收不到 pubsub：只落 Setting 行，不回写本进程 settings。"""
        Setting.objects.create(name=name, value=str(value), category="security_monitor")

    def test_converges_without_pubsub(self, superuser):
        from common.notifications import reconcile_monitor_thresholds

        self._persist_without_broadcast("SECURITY_MONITOR_DISK_USED_MAX", 90)
        import django.conf

        django.conf.settings.SECURITY_MONITOR_DISK_USED_MAX = 80  # 本进程停留旧值

        reconcile_monitor_thresholds()
        assert django.conf.settings.SECURITY_MONITOR_DISK_USED_MAX == 90

    def test_gate_skips_within_interval_then_expires(self, superuser, monkeypatch):
        """TTL 闸门：间隔内至多一次回读；缓存过期（时间推进）后再次对账读到最新值。"""
        import django.conf
        from django.core.cache import cache

        from common.notifications import (
            _THRESHOLD_RECONCILE_CACHE_KEY,
            reconcile_monitor_thresholds,
        )
        from settings.models import Setting

        self._persist_without_broadcast("SECURITY_MONITOR_DISK_USED_MAX", 90)
        django.conf.settings.SECURITY_MONITOR_DISK_USED_MAX = 80

        calls = []
        real_func = Setting.refresh_names.__func__

        def spy(cls, names):
            calls.append(list(names))
            return real_func(cls, names)

        monkeypatch.setattr(Setting, "refresh_names", classmethod(spy))

        reconcile_monitor_thresholds()
        assert django.conf.settings.SECURITY_MONITOR_DISK_USED_MAX == 90
        assert len(calls) == 1

        # 间隔内第二次调用命中闸门，不回读（库再变也不影响本窗口）
        Setting.objects.filter(name="SECURITY_MONITOR_DISK_USED_MAX").update(value="95")
        reconcile_monitor_thresholds()
        assert len(calls) == 1
        assert django.conf.settings.SECURITY_MONITOR_DISK_USED_MAX == 90

        # 缓存过期（时间推进）：闸门放行，重新收敛到最新落库值
        cache.delete(_THRESHOLD_RECONCILE_CACHE_KEY)
        reconcile_monitor_thresholds()
        assert len(calls) == 2
        assert django.conf.settings.SECURITY_MONITOR_DISK_USED_MAX == 95

    def test_health_summary_uses_reconciled_threshold(self, superuser):
        """面板健康总览（web 进程消费路径）同样经对账读到新阈值。"""
        import django.conf

        from system.utils.platform.monitor_metrics import collect_health_summary

        self._persist_without_broadcast("SECURITY_MONITOR_DISK_USED_MAX", 90)
        django.conf.settings.SECURITY_MONITOR_DISK_USED_MAX = 80

        summary = collect_health_summary()
        disk = next(item for item in summary["items"] if item["key"] == "disk_used")
        assert disk["threshold"] == 90
