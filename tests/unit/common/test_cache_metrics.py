#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""缓存命中率指标（xadmin_cache_requests_total）：记录口径与内核接线契约。

守护三件事：
1. 记录函数的计数与标签正确（cache=缓存名，result=hit/miss），且只认缓存名、不认缓存键；
2. 两套内核缓存（MagicCacheData / MagicCacheResponse）的命中与未命中各计一次；
3. 依赖缺失时降级为 no-op，不抛错、不影响缓存主流程。
"""

from types import SimpleNamespace

import pytest
from django.core.cache import cache

from common.base.magic import MagicCacheData, MagicCacheResponse

pytestmark = pytest.mark.django_db


def _cache_value(name: str, result: str) -> float:
    from common.metrics import _CACHE_REQUESTS

    return _CACHE_REQUESTS.labels(cache=name, result=result)._value.get()


class TestRecordCacheRequest:
    def test_hit_and_miss_increment_with_labels(self):
        from common.metrics import record_cache_request

        probe = "Cache_Metric_Label_Probe"
        before_hit = _cache_value(probe, "hit")
        before_miss = _cache_value(probe, "miss")
        record_cache_request(probe, "hit")
        record_cache_request(probe, "miss")
        assert _cache_value(probe, "hit") == before_hit + 1
        assert _cache_value(probe, "miss") == before_miss + 1

    def test_render_metrics_contains_metric(self):
        from common.metrics import record_cache_request, render_metrics

        record_cache_request("Cache_Metric_Render", "hit")
        output = render_metrics()[0].decode()
        assert "# TYPE xadmin_cache_requests_total counter" in output
        assert 'xadmin_cache_requests_total{cache="Cache_Metric_Render",result="hit"}' in output

    def test_noop_when_dependency_unavailable(self, monkeypatch):
        import common.metrics as metrics_module

        probe = "Cache_Metric_Noop_Probe"
        before = _cache_value(probe, "hit")
        monkeypatch.setattr(metrics_module, "_DEP_AVAILABLE", False)
        # 降级为 no-op：不抛错，也不记账
        metrics_module.record_cache_request(probe, "hit")
        assert _cache_value(probe, "hit") == before


class TestMagicCacheDataCacheMetric:
    def test_hit_and_miss_counted_once(self):
        calls = {"count": 0}

        @MagicCacheData.make_cache(timeout=60, key_func=lambda: "cm_probe")
        def cache_metric_data_probe():
            calls["count"] += 1
            return "cached-value"

        cache.delete("magic_cache_data_cache_metric_data_probe_cm_probe")
        before_miss = _cache_value("cache_metric_data_probe", "miss")
        before_hit = _cache_value("cache_metric_data_probe", "hit")

        assert cache_metric_data_probe() == "cached-value"  # 未命中 → 计算
        assert cache_metric_data_probe() == "cached-value"  # 命中

        assert calls["count"] == 1
        assert _cache_value("cache_metric_data_probe", "miss") == before_miss + 1
        assert _cache_value("cache_metric_data_probe", "hit") == before_hit + 1


class TestMagicCacheResponseCacheMetric:
    def test_hit_and_miss_counted_once(self):
        class _StubResponse:
            status_code = 200
            rendered_content = b"{}"

            def render(self):
                return None

            def items(self):
                return []

            def __setitem__(self, key, value):
                return None

        class CacheMetricProbeView:
            def get_renderer_context(self):
                return {}

            def finalize_response(self, request, response, *args, **kwargs):
                return response

        def cache_metric_response_probe(view_instance, request, *args, **kwargs):
            return _StubResponse()

        func_name = f"{CacheMetricProbeView.__name__}_{cache_metric_response_probe.__name__}"
        cache_response = MagicCacheResponse(timeout=60)
        MagicCacheResponse.invalid_cache(func_name)
        request = SimpleNamespace(query_params={})
        view = CacheMetricProbeView()

        before_miss = _cache_value(func_name, "miss")
        before_hit = _cache_value(func_name, "hit")

        def call():
            return cache_response.process_cache_response(
                view_instance=view,
                view_method=cache_metric_response_probe,
                request=request,
                args=(),
                kwargs={},
            )

        assert call().status_code == 200  # 未命中 → 回源并回写
        assert call().status_code == 200  # 命中

        assert _cache_value(func_name, "miss") == before_miss + 1
        assert _cache_value(func_name, "hit") == before_hit + 1
