# -*- coding: utf-8 -*-
"""CaptchaStore 模型单元测试（过期清理 / 池化取号 / 批量生成）。"""

import datetime

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from captcha.models import CaptchaStore

pytestmark = pytest.mark.django_db


def _make_store(challenge="abcd", expiration_delta=None):
    store = CaptchaStore(challenge=challenge, response=challenge)
    if expiration_delta is not None:
        store.expiration = timezone.now() + expiration_delta
    store.save()
    return store


class TestStr:
    def test_str_returns_challenge(self):
        store = _make_store(challenge="xyz9")
        assert str(store) == "xyz9"


class TestRemoveExpired:
    def test_expired_removed_and_live_kept(self):
        expired = _make_store(challenge="old1", expiration_delta=datetime.timedelta(minutes=-5))
        live = _make_store(challenge="new1", expiration_delta=datetime.timedelta(minutes=5))

        CaptchaStore.remove_expired()

        assert not CaptchaStore.objects.filter(pk=expired.pk).exists()
        assert CaptchaStore.objects.filter(pk=live.pk).exists()


class TestPick:
    @override_settings(CAPTCHA_GET_FROM_POOL=False)
    def test_pool_disabled_generates_new_key(self):
        assert CaptchaStore.objects.count() == 0
        key = CaptchaStore.pick()
        assert CaptchaStore.objects.filter(hashkey=key).exists()

    @override_settings(CAPTCHA_GET_FROM_POOL=True)
    def test_pool_empty_falls_back_to_generate(self):
        """池中无可用记录时应兜底重新生成验证码。"""
        key = CaptchaStore.pick()
        assert CaptchaStore.objects.filter(hashkey=key).exists()

    @override_settings(CAPTCHA_GET_FROM_POOL=True)
    def test_pool_returns_fresh_item(self):
        """过期时间晚于池化超时窗口的记录可直接复用。"""
        fresh = _make_store(
            challenge="pool1",
            expiration_delta=datetime.timedelta(minutes=int(settings.CAPTCHA_GET_FROM_POOL_TIMEOUT) + 10),
        )
        key = CaptchaStore.pick()
        assert key == fresh.hashkey

    @override_settings(CAPTCHA_GET_FROM_POOL=True)
    def test_pool_expired_item_triggers_fallback(self):
        """池中仅剩即将过期的记录时应兜底生成新验证码。"""
        _make_store(challenge="stale1", expiration_delta=datetime.timedelta(minutes=1))
        key = CaptchaStore.pick()
        assert key != "stale1"
        store = CaptchaStore.objects.get(hashkey=key)
        assert store.challenge != "stale1"


class TestCreatePool:
    def test_create_pool_generates_expected_count(self):
        CaptchaStore.create_pool(count=3)
        assert CaptchaStore.objects.count() == 3

    def test_create_pool_rejects_non_positive_count(self):
        with pytest.raises(AssertionError):
            CaptchaStore.create_pool(count=0)
