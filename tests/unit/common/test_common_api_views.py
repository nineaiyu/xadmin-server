# -*- coding: utf-8 -*-
"""common 通用 API 测试（资源缓存 / 国家区号 / 服务健康）。"""

import pytest

from common.cache.storage import CommonResourceIDsCache
from common.utils.country import COUNTRY_CALLING_CODES, COUNTRY_CALLING_CODES_ZH

pytestmark = pytest.mark.django_db

RESOURCES_CACHE_URL = "/api/common/resources/cache"
COUNTRIES_URL = "/api/common/countries"
HEALTH_URL = "/api/common/api/health"


class TestResourcesIDCache:
    def test_anonymous_denied(self, api_client):
        resp = api_client.post(RESOURCES_CACHE_URL, {"resources": ["1"]}, format="json")
        assert resp.status_code == 401

    def test_post_with_resources_caches_temporarily(self, auth_client):
        resp = auth_client.post(RESOURCES_CACHE_URL, {"resources": ["1", "2"]}, format="json")
        assert resp.status_code == 200
        spm = resp.json()["spm"]
        assert spm
        assert CommonResourceIDsCache(spm).get_storage_cache() == ["1", "2"]

    def test_post_without_resources_skips_cache(self, auth_client):
        """未传 resources 时应正常返回 spm，但不写入缓存。"""
        resp = auth_client.post(RESOURCES_CACHE_URL, {}, format="json")
        assert resp.status_code == 200
        spm = resp.json()["spm"]
        assert spm
        assert CommonResourceIDsCache(spm).get_storage_cache() is None


class TestCountryList:
    def test_english_locale_uses_default_codes(self, api_client):
        resp = api_client.get(COUNTRIES_URL, HTTP_ACCEPT_LANGUAGE="en-us")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data == COUNTRY_CALLING_CODES
        assert set(data[0].keys()) == {"name", "phone_code", "flag", "code"}

    def test_chinese_locale_uses_translated_codes(self, api_client):
        """Accept-Language 为 zh-hans 时应返回中文国别数据。"""
        resp = api_client.get(COUNTRIES_URL, HTTP_ACCEPT_LANGUAGE="zh-hans")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data == COUNTRY_CALLING_CODES_ZH
        assert data[0]["name"] != COUNTRY_CALLING_CODES[0]["name"]


class TestHealthCheck:
    def test_health_returns_probe_results(self, api_client):
        """默认探测路径：DB/Redis 应正常，无 worker 时 celery 状态为 False。"""
        resp = api_client.get(HEALTH_URL)
        assert resp.status_code == 200
        data = resp.json()
        assert data["db_status"] is True
        assert data["redis_status"] is True
        assert data["celery_status"] is False
        # status 只反映 DB/Redis 核心依赖
        assert data["status"] is True
        assert data["db_time"] >= 0
        assert data["redis_time"] >= 0
        assert data["celery_time"] >= 0

    def test_health_celery_skipped(self, api_client, settings):
        """显式跳过 celery 探测时耗时应为 0。"""
        settings.HEALTH_CHECK_SKIP_CELERY = True
        resp = api_client.get(HEALTH_URL)
        assert resp.status_code == 200
        data = resp.json()
        assert data["celery_status"] is False
        assert data["celery_time"] == 0.0
