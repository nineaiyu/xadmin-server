# -*- coding: utf-8 -*-
"""语序点选：难度数量 / 顺序敏感 / 容差。"""

import pytest

from captcha.backends.wordorder import TRADITIONAL_PHRASES, WordOrderBackend


@pytest.fixture(autouse=True)
def _track_disabled(settings):
    settings.CAPTCHA_TRACK_CHECK_ENABLED = False
    settings.CAPTCHA_CLICK_RADIUS = 20


class TestWordOrderBackend:
    @pytest.mark.parametrize(("difficulty", "count"), [("easy", 3), ("normal", 4), ("hard", 5)])
    def test_generate_counts(self, difficulty, count):
        challenge = WordOrderBackend().generate(difficulty)
        assert challenge.type == "wordorder"
        assert challenge.payload["count"] == count
        assert len(challenge.secret["points"]) == count
        assert len(challenge.secret["order"]) == count

    def test_traditional_phrases(self, settings):
        settings.CAPTCHA_CLICK_CHARSET = "traditional"
        challenge = WordOrderBackend().generate("normal")
        assert challenge.secret["order"] in TRADITIONAL_PHRASES["normal"]
        assert challenge.secret["asset_id"]

    def test_ordered_points_pass(self):
        backend = WordOrderBackend()
        challenge = backend.generate("normal")
        answer = {"points": challenge.secret["points"]}
        assert backend.verify(challenge.secret, answer, "normal") is True

    def test_reversed_order_fails(self):
        backend = WordOrderBackend()
        challenge = backend.generate("normal")
        answer = {"points": list(reversed(challenge.secret["points"]))}
        assert backend.verify(challenge.secret, answer, "normal") is False

    def test_difficulty_radius(self, settings):
        settings.CAPTCHA_CLICK_RADIUS = 20
        backend = WordOrderBackend()
        challenge = backend.generate("hard")
        target = challenge.secret["points"][0]
        # hard 半径收紧到 10px：偏移 12px 判错
        near = {"points": [{"x": target["x"] + 12, "y": target["y"]}] + challenge.secret["points"][1:]}
        assert backend.verify(challenge.secret, near, "hard") is False
