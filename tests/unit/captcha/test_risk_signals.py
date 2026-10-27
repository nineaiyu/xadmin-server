# -*- coding: utf-8 -*-
"""新增风险信号：abandon / type fixation / audio / ticket velocity / spread。"""

import pytest
from django.core.cache import cache

from captcha import signals


@pytest.fixture(autouse=True)
def _clean_cache():
    try:
        cache.delete_pattern("captcha_*")
    except AttributeError:
        pass
    yield
    try:
        cache.delete_pattern("captcha_*")
    except AttributeError:
        pass


class TestCounterSignals:
    def test_challenge_abandon(self):
        for _ in range(10):
            signals.record_challenge("10.1.1.1", fp="fp-1", captcha_type="slide")
        assert signals.challenge_abandon_hit("10.1.1.1") is True
        signals.record_verify("10.1.1.1")
        signals.record_verify("10.1.1.1")
        assert signals.challenge_abandon_hit("10.1.1.1") is False
        assert signals.challenge_abandon_hit("10.9.9.9") is False

    def test_type_fixation(self):
        for _ in range(10):
            signals.record_challenge("10.1.1.2", captcha_type="slide")
        assert signals.type_fixation_hit("10.1.1.2") is True
        for index in range(10):
            signals.record_challenge("10.1.1.3", captcha_type=("slide", "click")[index % 2])
        assert signals.type_fixation_hit("10.1.1.3") is False

    def test_audio_and_ticket_velocity(self):
        for _ in range(21):
            signals.record_audio("10.1.1.4")
        assert signals.audio_abuse_hit("10.1.1.4") is True
        for _ in range(6):
            signals.record_ticket_consume("10.1.1.5")
        assert signals.ticket_velocity_hit("10.1.1.5") is True

    def test_ip_fp_spread(self):
        for index in range(6):
            signals.record_challenge(f"10.2.{index}.1", fp=f"fp-{index}", captcha_type="slide")
        assert signals.ip_fp_spread_hit("10.2.0.1") is False  # 每个 /24 只有一个指纹
        for index in range(6):
            signals.record_challenge("10.4.0.1", fp=f"fp-x-{index}", captcha_type="slide")
        assert signals.ip_fp_spread_hit("10.4.0.1") is True
        for index in range(6):
            signals.record_challenge(f"10.3.{index}.1", fp="same-fp", captcha_type="slide")
        assert signals.fp_ip_spread_hit("same-fp") is True
