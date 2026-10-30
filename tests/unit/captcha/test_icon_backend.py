# -*- coding: utf-8 -*-
"""图标点选题型：生成 / 顺序校验 / 难度数量。"""

from captcha.backends.icon import IconBackend


def test_generate_payload_has_no_answer(settings):
    settings.CAPTCHA_SLIDE_SIZE = (320, 180)
    generated = IconBackend().generate()
    assert generated.type == "icon"
    assert "points" not in generated.payload
    assert generated.payload["background"].startswith("data:image/png;base64,")
    assert generated.payload["tip"].startswith("data:image/png;base64,")
    assert generated.payload["count"] == 3
    assert len(generated.secret["points"]) == 3


def test_verify_requires_order_and_hit_radius(settings):
    settings.CAPTCHA_SLIDE_SIZE = (320, 180)
    settings.CAPTCHA_CLICK_RADIUS = 20
    generated = IconBackend().generate()
    points = generated.secret["points"]
    assert IconBackend().verify(generated.secret, {"points": points}) is True
    assert IconBackend().verify(generated.secret, {"points": list(reversed(points))}) is False
    miss = [{"x": point["x"] + 200, "y": point["y"] + 200} for point in points]
    assert IconBackend().verify(generated.secret, {"points": miss}) is False


def test_hard_uses_more_targets(settings):
    settings.CAPTCHA_SLIDE_SIZE = (320, 180)
    generated = IconBackend().generate("hard")
    assert generated.payload["count"] == 4
    assert len(generated.secret["points"]) == 4
