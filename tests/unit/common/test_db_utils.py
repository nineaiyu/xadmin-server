# -*- coding: utf-8 -*-
"""common/core/db/utils.py：数据权限 Q 表达式构建与连接管理器。"""
import pytest
from django.db import connection, transaction
from django.db.models import Q

from common.core.db.utils import (
    RelatedManager,
    close_old_connections,
    open_db_connection,
    safe_atomic_db_connection,
    safe_db_connection,
)


def child_keys(q):
    return [c[0] for c in q.children if isinstance(c, tuple)]


class TestGetIpInQ:
    def test_single_ip_exact_match(self):
        q = RelatedManager.get_ip_in_q("ip", "10.0.0.1")
        assert child_keys(q) == ["ip__exact"]

    def test_ip_list_becomes_or(self):
        q = RelatedManager.get_ip_in_q("ip", ["10.0.0.1", "10.0.0.2"])
        assert len(q.children) == 2

    def test_wildcard_string_matches_prefix(self, redis_conn=None):
        # 已知行为：get_ip_in_q 的 ['*'] 守卫要求嵌套列表形态，普通 ["*"] 走 startswith
        q = RelatedManager.get_ip_in_q("ip", ["*"])
        assert child_keys(q) == ["ip__startswith"]

    def test_wildcard_nested_list_matches_all(self):
        assert RelatedManager.get_ip_in_q("ip", [["*"]]) == Q()

    def test_cidr_network_uses_in_lookup(self):
        q = RelatedManager.get_ip_in_q("ip", ["10.0.0.0/30"])
        assert child_keys(q) == ["ip__in"]

    def test_ip_range_uses_range_lookup(self):
        q = RelatedManager.get_ip_in_q("ip", ["10.0.0.1-10.0.0.5"])
        assert child_keys(q) == ["ip__range"]

    def test_prefix_without_full_octets_uses_startswith(self):
        q = RelatedManager.get_ip_in_q("ip", ["10.0"])
        assert child_keys(q) == ["ip__startswith"]

    def test_invalid_ip_entries_skipped(self):
        # "not-an-ip" 含连字符走网段分支抛 ValueError 被跳过，空串 continue
        assert RelatedManager.get_ip_in_q("ip", ["not-an-ip", ""]) == Q()


class TestGetFilterAttrsQs:
    def test_non_dict_entries_ignored(self):
        assert RelatedManager.get_filter_attrs_qs(["x", 1]) == []

    def test_missing_field_or_value_skipped(self):
        assert RelatedManager.get_filter_attrs_qs([{"field": "ip"}]) == []

    def test_match_all_returns_empty_q(self):
        filters = RelatedManager.get_filter_attrs_qs(
            [{"field": "ip", "value": "x", "match": "all"}]
        )
        assert filters == [Q()]

    def test_contains_lookup(self):
        filters = RelatedManager.get_filter_attrs_qs(
            [{"field": "name", "value": "abc", "match": "contains"}]
        )
        assert filters == [Q(name__contains="abc")]

    def test_regex_invalid_falls_back_to_empty_set(self):
        filters = RelatedManager.get_filter_attrs_qs(
            [{"field": "name", "value": "([bad", "match": "regex"}]
        )
        assert filters == [Q(pk__isnull=True)]

    def test_m2m_all_expands_per_value(self):
        filters = RelatedManager.get_filter_attrs_qs(
            [{"field": "roles", "value": [1, 2], "match": "m2m_all"}]
        )
        assert filters == [Q(roles__in=[1]), Q(roles__in=[2])]

    def test_m2m_single_lookup(self):
        filters = RelatedManager.get_filter_attrs_qs(
            [{"field": "roles", "value": 3, "match": "m2m"}]
        )
        assert filters == [Q(roles__in=[3])]

    def test_in_lookup_wildcard_matches_all(self):
        filters = RelatedManager.get_filter_attrs_qs(
            [{"field": "id", "value": ["*"], "match": "in"}]
        )
        assert filters == [Q()]

    def test_in_lookup_scalar_wrapped(self):
        filters = RelatedManager.get_filter_attrs_qs(
            [{"field": "id", "value": 7, "match": "in"}]
        )
        assert filters == [Q(id__in=[7])]

    def test_default_match_uses_exact_by_field_name(self):
        filters = RelatedManager.get_filter_attrs_qs(
            [{"field": "name", "value": "x"}]
        )
        assert filters == [Q(name__exact="x")]

    def test_wildcard_value_matches_all(self):
        filters = RelatedManager.get_filter_attrs_qs(
            [{"field": "name", "value": "*"}]
        )
        assert filters == [Q()]

    def test_exclude_negates(self):
        filters = RelatedManager.get_filter_attrs_qs(
            [{"field": "name", "value": "x", "exclude": True}]
        )
        assert filters == [~Q(name__exact="x")]


@pytest.mark.django_db
class TestConnectionManagers:
    def test_safe_db_connection_yields(self):
        with safe_db_connection():
            pass

    def test_safe_atomic_db_connection_in_atomic(self):
        with transaction.atomic():
            with safe_atomic_db_connection():
                assert connection.in_atomic_block

    def test_safe_atomic_db_connection_auto_close(self):
        with safe_atomic_db_connection(auto_close=True):
            pass

    @pytest.mark.django_db(transaction=True)
    def test_open_db_connection_executes_query(self):
        # open_db_connection 会在 finally 关闭连接，与 pytest-django 的
        # 事务回滚包装冲突，必须用 transactional 模式
        with open_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                assert cursor.fetchone()[0] == 1

    def test_close_old_connections_noop(self):
        close_old_connections()
