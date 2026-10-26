# -*- coding: utf-8 -*-
"""交互式验证码题型生成 / 容差 / 轨迹启发式单元测试。"""

import base64
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from captcha.backends.rotate import RotateBackend, normalize_angle
from captcha.backends.slider import SlideBackend
from captcha.backends.track import validate_track
from captcha.backends.wordclick import WordClickBackend


def _open_data_uri(uri: str) -> Image.Image:
    assert uri.startswith("data:image/png;base64,")
    return Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1])))


def _drag_track(duration: float = 200.0) -> list[dict[str, float]]:
    return [
        {"x": 0, "y": 0, "t": 0},
        {"x": 60, "y": 2, "t": duration / 2},
        {"x": 120, "y": 3, "t": duration},
    ]


@pytest.mark.parametrize("backend_type", ["slide", "rotate", "click"])
class TestGeneration:
    def test_payload_has_no_answer_and_image_is_png(self, settings, backend_type):
        settings.CAPTCHA_MODE = "interactive"
        settings.CAPTCHA_SLIDE_SIZE = (320, 180)
        settings.CAPTCHA_ROTATE_SIZE = (240, 240)
        backend = {"slide": SlideBackend(), "rotate": RotateBackend(), "click": WordClickBackend()}[backend_type]

        challenge = backend.generate()

        assert challenge.type == backend_type
        assert challenge.secret
        for key in ("x", "angle", "points"):
            assert key not in challenge.payload
        assert _open_data_uri(challenge.payload["background"]).format == "PNG"
        if backend_type == "slide":
            assert _open_data_uri(challenge.payload["piece"]).size == (320, 180)
        if backend_type == "click":
            assert _open_data_uri(challenge.payload["tip"]).format == "PNG"
            assert len(challenge.secret["points"]) == 3

    def test_two_generations_differ(self, settings, backend_type):
        settings.CAPTCHA_MODE = "interactive"
        backend = {"slide": SlideBackend(), "rotate": RotateBackend(), "click": WordClickBackend()}[backend_type]
        first = backend.generate()
        second = backend.generate()
        assert first.secret != second.secret


class TestSlidePieceCanvas:
    def test_piece_x_is_normalized_but_keeps_true_y(self, settings):
        settings.CAPTCHA_SLIDE_SIZE = (320, 180)
        challenge = SlideBackend().generate()
        piece = _open_data_uri(challenge.payload["piece"]).convert("RGBA")
        box = piece.getbbox()
        assert box is not None
        assert 0 <= box[0] < challenge.payload["piece_size"] // 2
        assert box[2] <= challenge.payload["piece_size"]
        assert box[1] > 0


class TestSlideEdgeSizes:
    def test_narrow_size_does_not_raise(self, settings):
        settings.CAPTCHA_SLIDE_SIZE = (120, 120)
        challenge = SlideBackend().generate()
        assert 0 <= challenge.secret["x"] <= 120 - challenge.payload["piece_size"]
        assert challenge.payload["width"] == 120


class TestSlideVerify:
    def test_tolerance_boundary(self, settings):
        settings.CAPTCHA_SLIDE_TOLERANCE = 4
        backend = SlideBackend()
        secret = {"x": 100}
        assert backend.verify(secret, {"x": 96})
        assert backend.verify(secret, {"x": 104})
        assert not backend.verify(secret, {"x": 95})
        assert not backend.verify(secret, {"x": 105})
        assert not backend.verify(secret, {"x": True})
        assert not backend.verify(secret, {})


class TestRotateVerify:
    def test_tolerance_and_wraparound(self, settings):
        settings.CAPTCHA_ROTATE_TOLERANCE = 4
        backend = RotateBackend()
        secret = {"angle": -42}
        assert backend.verify(secret, {"angle": -46})
        assert backend.verify(secret, {"angle": -38})
        assert not backend.verify(secret, {"angle": -47})
        # 179 与 -179 实际只差 2 度
        assert backend.verify({"angle": 179}, {"angle": -179})
        assert normalize_angle(180) == 180
        assert normalize_angle(-180) == 180

    def test_secret_angle_undoes_pre_rotation(self, settings):
        backend = RotateBackend()
        for _ in range(8):
            challenge = backend.generate()
            assert challenge.secret["angle"] == normalize_angle(challenge.secret["pre_rotation"])

    def test_rotate_image_has_no_black_corners(self, settings):
        settings.CAPTCHA_ROTATE_SIZE = (240, 240)
        challenge = RotateBackend().generate()
        image = _open_data_uri(challenge.payload["background"]).convert("RGB")
        width, height = image.size
        for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
            red, green, blue = image.getpixel(corner)
            assert max(red, green, blue) > 30, f"corner {corner} is near-black: {(red, green, blue)}"

    def test_css_clockwise_rotation_undoes_pillow_ccw(self):
        """Pillow 正角逆时针；前端 CSS 正角顺时针，答案必须是预转角本身。"""
        scene = Image.new("RGB", (80, 80), "white")
        ImageDraw.Draw(scene).rectangle((38, 5, 42, 12), fill="red")
        theta = 30
        rotated = scene.rotate(theta, resample=Image.Resampling.NEAREST, expand=False)
        # CSS rotate(theta) 等价于 Pillow rotate(-theta)
        restored = rotated.rotate(-theta, resample=Image.Resampling.NEAREST, expand=False)
        reds = [
            (x, y)
            for y in range(80)
            for x in range(80)
            if restored.getpixel((x, y))[0] > 200 and restored.getpixel((x, y))[1] < 80
        ]
        assert reds
        assert min(y for _, y in reds) < 25

    def test_pre_rotation_avoids_flat_bands(self, settings):
        backend = RotateBackend()
        for _ in range(12):
            secret = backend.generate().secret["angle"]
            flat = {0, 90, 180, -90, -180}
            assert all(abs(normalize_angle(secret - angle)) >= 15 for angle in flat)


class TestClickVerify:
    def test_ordered_points_and_radius(self, settings):
        settings.CAPTCHA_CLICK_RADIUS = 20
        backend = WordClickBackend()
        secret = {"points": [{"x": 50, "y": 50}, {"x": 150, "y": 60}, {"x": 250, "y": 70}]}
        exact = {"points": [{"x": 50, "y": 50}, {"x": 150, "y": 60}, {"x": 250, "y": 70}]}
        boundary = {"points": [{"x": 70, "y": 50}, {"x": 150, "y": 60}, {"x": 250, "y": 70}]}
        outside = {"points": [{"x": 71, "y": 50}, {"x": 150, "y": 60}, {"x": 250, "y": 70}]}
        swapped = {"points": [{"x": 150, "y": 60}, {"x": 50, "y": 50}, {"x": 250, "y": 70}]}
        assert backend.verify(secret, exact)
        assert backend.verify(secret, boundary)
        assert not backend.verify(secret, outside)
        assert not backend.verify(secret, swapped)
        assert not backend.verify(secret, {"points": exact["points"][:2]})


class TestTrackHeuristic:
    def test_valid_drag(self, settings):
        settings.CAPTCHA_TRACK_CHECK_ENABLED = True
        assert validate_track(_drag_track(), min_points=2)

    def test_too_short_or_missing(self):
        assert not validate_track(None)
        assert not validate_track([])
        assert not validate_track([{"x": 0, "y": 0, "t": 0}])

    def test_duration_bounds(self):
        assert not validate_track(_drag_track(duration=50))
        assert not validate_track(_drag_track(duration=70_000))
        assert validate_track(_drag_track(duration=20_000))

    def test_reversed_time_and_teleport(self):
        reversed_time = [{"x": 0, "y": 0, "t": 100}, {"x": 10, "y": 0, "t": 50}, {"x": 20, "y": 0, "t": 160}]
        teleport = [{"x": 0, "y": 0, "t": 0}, {"x": 500, "y": 0, "t": 1}, {"x": 520, "y": 0, "t": 160}]
        fast_human = [{"x": 0, "y": 0, "t": 0}, {"x": 250, "y": 0, "t": 15}, {"x": 260, "y": 0, "t": 160}]
        assert not validate_track(reversed_time)
        assert not validate_track(teleport)
        assert validate_track(fast_human)

    def test_bad_point_shapes(self):
        assert not validate_track([{"x": "0", "y": 0, "t": 0}, {"x": 1, "y": 0, "t": 100}])
        assert not validate_track([{"x": 0, "y": 0}, {"x": 1, "y": 0, "t": 100}])
