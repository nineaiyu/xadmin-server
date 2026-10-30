# -*- coding: utf-8 -*-
"""阶梯处置：三维度计数 / 冷却 / 清零。"""

import pytest
from django.core.cache import cache

from captcha import disposal


@pytest.fixture(autouse=True)
def _clean_cache():
    try:
        cache.delete_pattern("captcha_*")
    except AttributeError:
        pass
    yield


def _dims():
    return disposal.dimension_keys(ip_hash="iph", action="identity.login.basic", fp="fph")


class TestDisposalLadder:
    def test_parse_steps(self, settings):
        assert disposal.parse_steps() == (3, 8, 20)
        settings.CAPTCHA_FAIL_STEP = "0,0,0"
        assert disposal.parse_steps() == (0, 0, 0)
        settings.CAPTCHA_FAIL_STEP = "bad"
        assert disposal.parse_steps() == (0, 0, 0)

    def test_first_rung_forces_hard_only(self, settings):
        settings.CAPTCHA_FAIL_STEP = "3,8,20"
        for _ in range(2):
            assert disposal.register_failure(_dims())["force_hard"] is False
        result = disposal.register_failure(_dims())
        assert result == {"force_hard": True, "cooldown": 0}
        assert disposal.cooldown_remaining(_dims()) == 0

    def test_forced_hard_reads_first_rung(self, settings):
        settings.CAPTCHA_FAIL_STEP = "3,8,20"
        assert disposal.forced_hard(_dims()) is False
        for _ in range(3):
            disposal.register_failure(_dims())
        assert disposal.forced_hard(_dims()) is True
        disposal.clear_failures(_dims())
        assert disposal.forced_hard(_dims()) is False

    def test_second_rung_cooldown(self, settings):
        settings.CAPTCHA_FAIL_STEP = "3,8,20"
        for _ in range(8):
            result = disposal.register_failure(_dims())
        assert result["cooldown"] == 900
        assert disposal.cooldown_remaining(_dims()) > 0

    def test_success_clears(self, settings):
        settings.CAPTCHA_FAIL_STEP = "3,8,20"
        for _ in range(8):
            disposal.register_failure(_dims())
        disposal.clear_failures(_dims())
        assert disposal.cooldown_remaining(_dims()) == 0
        assert disposal.register_failure(_dims())["cooldown"] == 0

    def test_action_dimension_is_scoped_to_client(self, settings):
        settings.CAPTCHA_FAIL_STEP = "3,8,20"
        client_a = disposal.dimension_keys(ip_hash="iph-a", action="identity.login.basic", fp="fph-a")
        client_b = disposal.dimension_keys(ip_hash="iph-b", action="identity.login.basic", fp="fph-b")
        assert not any(dim == "action" for dim, _value in client_a)
        for _ in range(8):
            disposal.register_failure(client_a)
        assert disposal.cooldown_remaining(client_a) > 0
        assert disposal.cooldown_remaining(client_b) == 0

    def test_disabled_ladder(self, settings):
        settings.CAPTCHA_FAIL_STEP = "0,0,0"
        for _ in range(30):
            assert disposal.register_failure(_dims()) == {"force_hard": False, "cooldown": 0}
