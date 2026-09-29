# -*- coding: utf-8 -*-
"""知识库索引元数据短缓存：TTL 命中/过期、按 scope 失效、异常不缓存的单元级守护。"""

import pytest

from ai.utils import index_meta


class TestCachedMetaRows:
    def setup_method(self):
        index_meta.invalidate_index_meta()

    def teardown_method(self):
        index_meta.invalidate_index_meta()

    def test_loader_called_once_within_ttl(self):
        calls = []

        def loader():
            calls.append(1)
            return ["row"]

        assert index_meta.cached_meta_rows("scope-a", loader) == ["row"]
        assert index_meta.cached_meta_rows("scope-a", loader) == ["row"]
        assert len(calls) == 1, "TTL 窗口内同一 scope 只加载一次"

    def test_expired_ttl_reloads(self, monkeypatch):
        calls = []
        monkeypatch.setattr(index_meta, "META_CACHE_TTL_SECONDS", 0)

        def loader():
            calls.append(1)
            return ["row"]

        index_meta.cached_meta_rows("scope-a", loader)
        index_meta.cached_meta_rows("scope-a", loader)
        assert len(calls) == 2, "TTL 过期后必须重新加载"

    def test_invalidate_by_scope_and_all(self):
        calls = {"a": 0, "b": 0}

        def loader(scope):
            def _load():
                calls[scope] += 1
                return [scope]

            return _load

        assert index_meta.cached_meta_rows("a", loader("a")) == ["a"]
        assert index_meta.cached_meta_rows("b", loader("b")) == ["b"]

        index_meta.invalidate_index_meta("a")
        index_meta.cached_meta_rows("a", loader("a"))
        index_meta.cached_meta_rows("b", loader("b"))
        assert calls == {"a": 2, "b": 1}, "按 scope 失效只清指定 scope"

        index_meta.invalidate_index_meta()
        index_meta.cached_meta_rows("a", loader("a"))
        index_meta.cached_meta_rows("b", loader("b"))
        assert calls == {"a": 3, "b": 2}, "无参失效清全部 scope"

    def test_loader_error_not_cached(self):
        calls = []

        def boom():
            calls.append(1)
            raise RuntimeError("db down")

        with pytest.raises(RuntimeError):
            index_meta.cached_meta_rows("scope-a", boom)
        with pytest.raises(RuntimeError):
            index_meta.cached_meta_rows("scope-a", boom)
        assert len(calls) == 2, "loader 异常不写缓存（下次仍重新尝试）"
