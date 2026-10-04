# -*- coding: utf-8 -*-
"""Setting 删除热更闭环测试（T03-07）。

删除 Setting 行（含绕过 UI 的批量删除）必须回收运行时值：恢复静态配置默认值
并向其余进程广播。此前仅挂 post_save——直接 DELETE 后旧值在进程内继续生效。
"""

import json

import pytest
from django.conf import settings as dj_settings

from server.const import CONFIG
from settings.models import Setting

pytestmark = pytest.mark.django_db

_MISSING = object()

# 测试触碰的运行时键：结束后回滚 setattr 泄漏（pytest-django 只回滚 DB 事务）
_RESTORE_KEYS = ["SITE_URL", "VERIFY_CODE_TTL", "VERIFY_CODE_LIMIT", "TOTALLY_CUSTOM_XADMIN_KEY"]


@pytest.fixture(autouse=True)
def _snapshot_runtime_settings():
    original = {key: getattr(dj_settings, key, _MISSING) for key in _RESTORE_KEYS}
    yield
    for key, value in original.items():
        if value is _MISSING:
            if hasattr(dj_settings, key):
                delattr(dj_settings, key)
        else:
            setattr(dj_settings, key, value)


@pytest.fixture
def published(monkeypatch):
    """拦截 pub/sub 发布（不发真实 Redis），记录载荷。

    必须替换 signal_handlers 模块全局（handler 调用期读取）：conftest 的
    session 级 _isolate_settings_pubsub 已把该名字换成 Noop 桩，直接 patch
    原 LazyObject 对象会被绕开。
    """
    import settings.signal_handlers as signal_handlers

    calls = []

    class _RecorderPubSub:
        def publish(self, data):
            calls.append(tuple(data))
            return True

    monkeypatch.setattr(signal_handlers, "setting_pub_sub", _RecorderPubSub())
    return calls


class TestDefaultValue:
    def test_known_key_returns_static_default(self):
        assert Setting.default_value("SITE_URL") == CONFIG.get("SITE_URL")

    def test_unknown_key_falls_back_to_none(self):
        assert Setting.default_value("TOTALLY_UNKNOWN_KEY_XYZ") is None


class TestDeleteRestoresRuntimeValue:
    def test_delete_restores_static_default(self, published):
        row = Setting.objects.create(name="SITE_URL", value=json.dumps("https://hot-update.example.com"))
        row.refresh_setting()
        assert dj_settings.SITE_URL == "https://hot-update.example.com"

        row.delete()

        assert dj_settings.SITE_URL == CONFIG.get("SITE_URL")
        # published[0] 是 create 的 post_save 保存发布（保存链路既有行为）；
        # 删除发布的载荷 = (name, 静态默认值)，恢复值落在最后
        assert published[-1] == ("SITE_URL", CONFIG.get("SITE_URL"))

    def test_delete_unknown_key_falls_back_to_none(self, published):
        """CONFIG 未知键（运行期自建自定义键）：回收为 None（setattr 语义统一）。"""
        row = Setting.objects.create(name="TOTALLY_CUSTOM_XADMIN_KEY", value=json.dumps("v1"))
        row.refresh_setting()
        assert dj_settings.TOTALLY_CUSTOM_XADMIN_KEY == "v1"

        row.delete()

        assert dj_settings.TOTALLY_CUSTOM_XADMIN_KEY is None
        assert published[-1] == ("TOTALLY_CUSTOM_XADMIN_KEY", None)

    def test_bulk_delete_restores_each(self, published):
        """queryset.bulk delete（批量删除/admin）逐行触发 post_delete，逐键回收。"""
        names = ["VERIFY_CODE_TTL", "VERIFY_CODE_LIMIT"]
        for name in names:
            Setting.objects.create(name=name, value=json.dumps(999999)).refresh_setting()
            assert getattr(dj_settings, name) == 999999

        Setting.objects.filter(name__in=names).delete()

        for name in names:
            assert getattr(dj_settings, name) == CONFIG.get(name)
        # 前两条是 create 的保存发布；批量删除逐行触发 post_delete，逐键广播恢复值
        assert sorted(published[-2:]) == sorted((name, CONFIG.get(name)) for name in names)

    def test_model_delete_and_queryset_delete_both_publish(self, published):
        default = CONFIG.get("VERIFY_CODE_LENGTH")
        Setting.objects.create(name="VERIFY_CODE_LENGTH", value=json.dumps(4))
        Setting.objects.get(name="VERIFY_CODE_LENGTH").delete()
        Setting.objects.create(name="VERIFY_CODE_LENGTH", value=json.dumps(6))
        Setting.objects.filter(name="VERIFY_CODE_LENGTH").delete()
        # 单行 delete 与 queryset 批量 delete 都触发 post_delete 广播恢复值
        assert published[1] == ("VERIFY_CODE_LENGTH", default)
        assert published[-1] == ("VERIFY_CODE_LENGTH", default)
