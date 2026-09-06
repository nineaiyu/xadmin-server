# -*- coding: utf-8 -*-
"""common/cache/redis.py：Redis 数据结构封装（基于测试 FakeRedis 后端）。"""
import pytest

from common.cache.redis import (
    CacheHash,
    CacheList,
    CacheRedis,
    CacheSet,
    CacheSortedSet,
    format_input,
    format_return,
)


def test_format_return_decodes_json_bytes():
    assert format_return(b'{"a": 1}') == {"a": 1}
    assert format_return('["x"]') == ["x"]
    assert format_return(b'123') == 123


def test_format_return_non_json_passthrough():
    assert format_return(b"plain") == "plain"
    assert format_return("plain") == "plain"


def test_format_input_json_dumps():
    assert format_input({"a": 1}) == '{"a": 1}'
    assert format_input(["x"]) == '["x"]'


def test_format_input_non_serializable_passthrough():
    obj = object()
    assert format_input(obj) is obj


@pytest.fixture
def redis_conn():
    from django_redis import get_redis_connection

    conn = get_redis_connection("default")
    yield conn
    conn.flushall()


class TestCacheList:
    def test_push_pop_roundtrip(self, redis_conn):
        cache = CacheList("cl:roundtrip", timeout=60)
        cache.push({"k": 1})
        cache.push("plain")
        assert cache.len() == 2
        assert cache.get_all() == ["plain", {"k": 1}]  # lpush 后进先出排列
        # rpop 从尾部弹出 → 先进先出，先弹出最早写入的 {"k": 1}
        assert cache.pop() == {"k": 1}
        assert cache.pop() == "plain"
        assert cache.pop() is None

    def test_auto_ltrim_respects_max_size(self, redis_conn):
        cache = CacheList("cl:ltrim", max_size=2)
        for i in range(5):
            cache.push(i)
        assert cache.len() <= 2

    def test_pop_empty_returns_none(self, redis_conn):
        assert CacheList("cl:empty").pop() is None

    def test_delete_clears_key(self, redis_conn):
        cache = CacheList("cl:del")
        cache.push(1)
        cache.delete()
        assert cache.len() == 0


class TestCacheSet:
    def test_push_int_members_and_get_all(self, redis_conn):
        cache = CacheSet("cs:basic")
        cache.push(2)
        cache.push(2)
        cache.push(3)
        assert cache.get_all() == {2, 3}
        assert cache.count() == 2

    def test_exist_and_pop_use_same_encoding_as_push(self, redis_conn):
        # push 存储的是 format_input 编码值；int 编码后与原值查询一致
        # sismember 返回 1/0 而非布尔
        cache = CacheSet("cs:exist")
        cache.push(7)
        assert cache.exist(7) == 1
        cache.pop(7)
        assert cache.exist(7) == 0

    def test_string_member_exist_mismatch_quirk(self, redis_conn):
        # 已知行为：字符串成员以 JSON 编码存储（'"x"'），exist 用原值查询不命中
        cache = CacheSet("cs:quirk")
        cache.push("x")
        assert cache.exist("x") == 0

    def test_delete(self, redis_conn):
        cache = CacheSet("cs:del")
        cache.push(1)
        cache.delete()
        assert cache.count() == 0


class TestCacheSortedSet:
    def test_push_dict_and_get_members(self, redis_conn):
        cache = CacheSortedSet("cz:dict")
        cache.push({"a": "1", "b": "2"})
        assert cache.count() == 2
        assert cache.get_all() == ["b", "a"]  # zrevrange 按分数倒序

    def test_push_scalar_scores_by_time(self, redis_conn):
        cache = CacheSortedSet("cz:scalar")
        cache.push("first")
        cache.push("second")
        members = cache.get_all()
        assert members == ["second", "first"]

    def test_get_members_with_scores(self, redis_conn):
        cache = CacheSortedSet("cz:scores")
        cache.push({"a": "5"})
        data = cache.get_members(with_scores=True)
        assert data == [{"a": 5}]

    def test_exist_uses_falsy_rank_quirk(self, redis_conn):
        cache = CacheSortedSet("cz:exist")
        cache.push({"a": "1", "b": "2"})
        # 已知行为：exist 基于 bool(zrank)，rank 0 的最低分成员被视为不存在
        assert cache.exist("a") is False  # a 分数最低，rank 0
        assert cache.exist("b") is True   # b 分数最高，rank 1


class TestCacheHash:
    def test_push_get_all(self, redis_conn):
        cache = CacheHash("ch:basic")
        cache.push("k1", {"v": 1})
        cache.push("k2", "text")
        assert cache.get_all() == {"k1": {"v": 1}, "k2": "text"}
        assert cache.get("k1") == {"v": 1}
        assert cache.count() == 2

    def test_pop_and_delete(self, redis_conn):
        cache = CacheHash("ch:pop")
        cache.push("k", 1)
        cache.pop("k")
        assert cache.get("k") is None
        cache.push("k2", 2)
        cache.delete()
        assert cache.count() == 0


class TestCacheRedisBase:
    def test_lock_context(self, redis_conn):
        cache = CacheRedis("base:lock")
        # 名字由 CacheRedis 拼接，调用方只传锁参数
        with cache.lock(timeout=5):
            pass  # 可正常获取与释放

    def test_expire(self, redis_conn):
        cache = CacheRedis("base:expire")
        redis_conn.set(cache.key, "v")
        assert cache.expire(60) is True
