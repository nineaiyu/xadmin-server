# -*- coding: utf-8 -*-
"""PERF-07：search-columns / search-fields 关联列 choices 行数上限测试。

覆盖：
1. 未超上限（小表）时行为不变，不产生 choices_truncated；
2. 超上限时只返回前 N 条并带出 choices_truncated 标记；
3. 上限可通过系统配置 SEARCH_CHOICES_MAX_COUNT 调整；
4. 显式 cutoff 时沿用调用方行为，不做二次截断；
5. 配置读取失败时的降级行为。
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from common.core.config import SysConfig
from common.core.fields import BasePrimaryKeyRelatedField, get_search_choices_max_count
from system.models import UserInfo

pytestmark = pytest.mark.django_db

TOTAL_USERS = 230


@pytest.fixture(autouse=True)
def _reset_choices_max_cache():
    """进程内短 TTL 缓存跨测试必须重置，否则 monkeypatch 的配置值不生效"""
    import common.core.fields as fields_mod

    fields_mod._CHOICES_MAX_CACHE = {"value": None, "expires": 0.0}
    yield
    fields_mod._CHOICES_MAX_CACHE = {"value": None, "expires": 0.0}


@pytest.fixture
def many_users(db):
    UserInfo.objects.bulk_create([
        UserInfo(username=f"bulk{i:03d}", nickname=f"u{i}",
                 password="md5$unused$hash-for-bulk-creation")  # noqa: S106
        for i in range(TOTAL_USERS)
    ])
    return UserInfo.objects.filter(username__startswith="bulk")


def _field(queryset):
    return BasePrimaryKeyRelatedField(
        attrs=["pk", "username"], queryset=queryset, many=False
    )


class TestChoicesMaxCount:
    def test_default_config_value(self):
        assert get_search_choices_max_count() == 200
        assert SysConfig.SEARCH_CHOICES_MAX_COUNT == 200

    def test_small_queryset_not_truncated(self, many_users):
        field = _field(UserInfo.objects.filter(username__in=["bulk000"]))
        field.is_column = True
        choices = field.get_choices()
        assert len(choices) == 1
        assert not getattr(field, "choices_truncated", False)

    def test_large_queryset_truncated(self, many_users):
        """默认上限 200：230 个用户只返回前 200 条，并带截断标记"""
        field = _field(many_users)
        field.is_column = True
        with CaptureQueriesContext(connection) as ctx:
            choices = field.get_choices()

        assert len(choices) == 200
        assert getattr(field, "choices_truncated") is True
        # 序列化成本被截断：SQL 带出 LIMIT 201（200 条 + 1 行用于判定截断）
        assert any("LIMIT 201" in q["sql"] for q in ctx.captured_queries)

    def test_truncation_uses_config_value(self, many_users, monkeypatch):
        monkeypatch.setattr(type(SysConfig), "SEARCH_CHOICES_MAX_COUNT",
                            property(lambda self: 10), raising=False)
        field = _field(many_users)
        field.is_column = True
        choices = field.get_choices()
        assert len(choices) == 10
        assert getattr(field, "choices_truncated") is True

    def test_dict_choices_truncated(self, many_users, monkeypatch):
        """非 column（dict 形式 choices）同样被截断并标记"""
        monkeypatch.setattr(type(SysConfig), "SEARCH_CHOICES_MAX_COUNT",
                            property(lambda self: 10), raising=False)
        field = _field(many_users)
        choices = field.get_choices()
        assert len(choices) == 10
        assert getattr(field, "choices_truncated") is True

    def test_explicit_cutoff_not_overridden(self, many_users):
        field = _field(many_users)
        field.is_column = True
        choices = field.get_choices(cutoff=3)
        assert len(choices) == 3
        assert not getattr(field, "choices_truncated", False)

    def test_config_fallback_on_error(self, monkeypatch):
        """配置读取异常时退回默认值，不影响下拉数据（异常不缓存）"""
        import common.core.fields as fields_mod

        monkeypatch.setattr(fields_mod, "_CHOICES_MAX_CACHE", {"value": None, "expires": 0.0})
        monkeypatch.setattr(type(SysConfig), "SEARCH_CHOICES_MAX_COUNT",
                            property(lambda self: (_ for _ in ()).throw(RuntimeError("boom"))),
                            raising=False)
        assert get_search_choices_max_count() == 200
        # 再次调用仍不使用缓存的异常结果
        assert get_search_choices_max_count() == 200

    def test_config_value_is_short_lived_cached(self, monkeypatch):
        """同一请求内多个关联字段共享一次配置读取（短 TTL 进程内缓存）"""
        import common.core.fields as fields_mod

        monkeypatch.setattr(fields_mod, "_CHOICES_MAX_CACHE", {"value": None, "expires": 0.0})
        assert fields_mod.get_search_choices_max_count() == fields_mod.get_search_choices_max_count()
        assert fields_mod._CHOICES_MAX_CACHE["value"] == 200
