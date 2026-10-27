# -*- coding: utf-8 -*-
"""风险编排辅助：难度/题型选择与票据档位。"""

from captcha import flow


class TestFlow:
    def test_difficulty_mapping(self):
        assert flow.difficulty_for("hard") == "hard"
        assert flow.difficulty_for("light") == "easy"
        assert flow.difficulty_for("challenge") == "normal"

    def test_ticket_mode_mapping(self):
        assert flow.ticket_mode_for("allow") == "allow"
        assert flow.ticket_mode_for("light") == "light"
        assert flow.ticket_mode_for("hard") == "hard"
        assert flow.ticket_mode_for("challenge") == "challenge"
        assert flow.ticket_mode_for("deny") == "challenge"

    def test_light_uses_pow(self, settings):
        settings.CAPTCHA_POW_ENABLED = True
        ctx = flow.RiskContext(scene="login", ip="8.8.8.8")
        selected, difficulty = flow.pick_type(ctx, ["slide", "click"], None, "light")
        assert selected == "pow"
        assert difficulty == "hard"

    def test_light_falls_back_without_pow(self, settings):
        settings.CAPTCHA_POW_ENABLED = False
        ctx = flow.RiskContext(scene="login", ip="8.8.8.8")
        selected, difficulty = flow.pick_type(ctx, ["slide", "click"], None, "light")
        assert selected in ("slide", "click")
        assert difficulty == "normal"

    def test_hard_prefers_wordorder(self):
        ctx = flow.RiskContext(scene="login", ip="8.8.8.8")
        selected, difficulty = flow.pick_type(ctx, ["slide", "click", "wordorder"], None, "hard")
        assert selected == "wordorder"
        assert difficulty == "hard"

    def test_requested_type_wins(self):
        ctx = flow.RiskContext(scene="login", ip="8.8.8.8")
        selected, _ = flow.pick_type(ctx, ["slide", "click"], "click", "challenge")
        assert selected == "click"

    def test_hard_falls_back_without_wordorder(self):
        ctx = flow.RiskContext(scene="login", ip="8.8.8.8")
        selected, difficulty = flow.pick_type(ctx, ["slide", "click"], None, "hard")
        assert selected in ("slide", "click")
        assert difficulty == "hard"

    def test_weighted_rotation_avoids_repeat(self, settings):
        from django.core.cache import cache

        settings.CAPTCHA_TYPE_WEIGHTS = {"slide": 1, "click": 1}
        try:
            cache.delete_pattern("captcha_type_recent_*")
        except AttributeError:
            pass
        ctx = flow.RiskContext(scene="login", ip="8.8.8.8", fp="fph")
        first, _ = flow.pick_type(ctx, ["slide", "click"], None, "challenge")
        second, _ = flow.pick_type(ctx, ["slide", "click"], None, "challenge")
        assert first != second

    def test_allow_uses_easy_pow(self):
        ctx = flow.RiskContext(scene="login", ip="8.8.8.8")
        selected, difficulty = flow.pick_type(ctx, ["slide", "click"], None, "allow")
        assert selected == "pow"
        assert difficulty == "easy"

    def test_explicit_image_fallback_wins_over_allow_and_light(self, settings):
        settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = True
        ctx = flow.RiskContext(scene="login", ip="8.8.8.8")
        for decision in ("allow", "light"):
            selected, difficulty = flow.pick_type(ctx, ["slide", "click", "image"], "image", decision)
            assert selected == "image"
            assert difficulty == ("normal" if decision == "allow" else "easy")

    def test_hard_never_downgrades_to_image(self, settings):
        settings.CAPTCHA_IMAGE_FALLBACK_ENABLED = True
        ctx = flow.RiskContext(scene="login", ip="8.8.8.8")
        selected, _ = flow.pick_type(ctx, ["slide", "click", "image"], "image", "hard")
        assert selected != "image"

    def test_hard_ignores_requested_easy_type(self):
        ctx = flow.RiskContext(scene="login", ip="8.8.8.8")
        selected, difficulty = flow.pick_type(ctx, ["slide", "click", "wordorder"], "slide", "hard")
        assert selected == "wordorder"
        assert difficulty == "hard"

    def test_stored_result_roundtrip(self):
        result = flow.stored_result({"decision": "hard", "risk": 75, "risk_reasons": ["env_headless"]})
        assert result.decision == "hard"
        assert result.score == 75
        assert result.reasons == ["env_headless"]
