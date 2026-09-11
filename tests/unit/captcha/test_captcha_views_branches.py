# -*- coding: utf-8 -*-
"""captcha 视图分支测试（图片渲染分支 / 音频分支 / 字体配置异常）。"""

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, override_settings
from PIL import ImageFont

from captcha.models import CaptchaStore
from captcha.views import captcha_image, getsize

pytestmark = pytest.mark.django_db

rf = RequestFactory()
# 模块导入时保存真实字体路径，避免被 override_settings 覆盖后取到假路径
REAL_FONT_PATH = settings.CAPTCHA_FONT_PATH


def _make_store(challenge="ab12"):
    store = CaptchaStore.objects.create(challenge=challenge, response=challenge)
    return store.hashkey


class TestGetSize:
    class _OffsetFont:
        """同时具备 getsize/getoffset 的旧式字体接口。"""

        def getsize(self, text):
            return (len(text) * 6, 12)

        def getoffset(self, text):
            return (2, 3)

    class _LegacyFont:
        """仅有 getsize 的最老字体接口。"""

        def getsize(self, text):
            return (len(text) * 6, 12)

    def test_font_with_getoffset(self):
        assert getsize(self._OffsetFont(), "ab") == (14, 15)

    def test_legacy_font_getsize_only(self):
        assert getsize(self._LegacyFont(), "ab") == (12, 12)


class TestCaptchaImageBranches:
    def test_scale2_enabled_returns_png(self, api_client):
        """CAPTCHA_2X_IMAGE 开启时 @2x 图片应正常渲染。"""
        key = _make_store()
        resp = api_client.get(f"/api/system/captcha/image/{key}@2/")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"

    @override_settings(CAPTCHA_2X_IMAGE=False)
    def test_scale2_disabled_returns_404(self, api_client):
        """CAPTCHA_2X_IMAGE 关闭时 @2x 图片应返回 404。"""
        key = _make_store()
        resp = api_client.get(f"/api/system/captcha/image/{key}@2/")
        assert resp.status_code == 404

    @override_settings(CAPTCHA_FONT_PATH=[settings.CAPTCHA_FONT_PATH])
    def test_font_path_as_list(self, api_client):
        """字体路径为列表时应随机选取其中一个。"""
        key = _make_store()
        resp = api_client.get(f"/api/system/captcha/image/{key}/")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"

    def test_invalid_font_path_raises(self):
        """字体路径配置既非字符串也非列表时应抛 ImproperlyConfigured。"""
        key = _make_store()
        with override_settings(CAPTCHA_FONT_PATH=None):
            with pytest.raises(ImproperlyConfigured):
                captcha_image(rf.get("/x/"), key)

    @override_settings(CAPTCHA_FONT_PATH="/tmp/not-exist-font.pil")
    def test_non_ttf_font_uses_bitmap_loader(self, api_client):
        """非 .ttf 结尾的字体路径应走 ImageFont.load 位图字体加载分支。"""
        key = _make_store()
        real_font = ImageFont.truetype(REAL_FONT_PATH, settings.CAPTCHA_FONT_SIZE)
        with mock.patch("captcha.views.ImageFont.load", return_value=real_font) as fake_load:
            resp = api_client.get(f"/api/system/captcha/image/{key}/")
        assert resp.status_code == 200
        fake_load.assert_called_once()

    @override_settings(CAPTCHA_IMAGE_SIZE=None)
    def test_no_image_size_uses_text_metrics(self, api_client):
        """未配置图片尺寸时应按文字测量尺寸自动裁剪画布。"""
        key = _make_store()
        resp = api_client.get(f"/api/system/captcha/image/{key}/")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"

    def test_punctuation_merged_with_previous_char(self, api_client):
        """挑战文本中的标点应与前一字符合并绘制，不单独占位。"""
        key = _make_store(challenge="a-b")
        resp = api_client.get(f"/api/system/captcha/image/{key}/")
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"


FLITE = "/usr/bin/true"


class TestCaptchaAudioBranches:
    @override_settings(CAPTCHA_FLITE_PATH=FLITE)
    def test_store_missing_returns_410(self, api_client):
        resp = api_client.get("/api/system/captcha/audio/notexistkey.wav")
        assert resp.status_code == 410

    @override_settings(CAPTCHA_FLITE_PATH=FLITE, CAPTCHA_CHALLENGE_FUNCT="captcha.helpers.random_char_challenge")
    def test_non_math_challenge_text_joined_by_comma(self, api_client):
        """非算数挑战时应把挑战文本逐字符用逗号拼接后交给 flite。"""
        key = _make_store(challenge="ab")
        wav_path = os.path.join(tempfile.gettempdir(), f"{key}.wav")
        self._clean(wav_path)
        with mock.patch("captcha.views.subprocess.call", return_value=0) as fake_call:
            resp = api_client.get(f"/api/system/captcha/audio/{key}.wav")
        assert resp.status_code == 404  # /usr/bin/true 不会产出 wav，回落 404
        args = fake_call.call_args[0][0]
        assert args[:3] == [FLITE, "-t", "a, b"]
        assert args[4] == wav_path

    @override_settings(
        CAPTCHA_FLITE_PATH=FLITE,
        CAPTCHA_CHALLENGE_FUNCT="captcha.helpers.math_challenge",
    )
    def test_math_challenge_words_replaced(self, api_client):
        """算数挑战时应把运算符替换为英文单词（minus 等）。"""
        key = _make_store(challenge="3-1=")
        with mock.patch("captcha.views.subprocess.call", return_value=0) as fake_call:
            resp = api_client.get(f"/api/system/captcha/audio/{key}.wav")
        assert resp.status_code == 404
        args = fake_call.call_args[0][0]
        assert args[:3] == [FLITE, "-t", "3minus1="]

    @override_settings(CAPTCHA_FLITE_PATH=FLITE, CAPTCHA_CHALLENGE_FUNCT="captcha.helpers.random_char_challenge")
    def test_wav_generated_returns_audio_response(self, api_client):
        """flite 产出 wav 文件时应以 audio/wav 附件返回。"""
        key = _make_store(challenge="cd")
        wav_path = os.path.join(tempfile.gettempdir(), f"{key}.wav")
        self._clean(wav_path)
        try:
            Path(wav_path).write_bytes(b"RIFFd32d")
            resp = api_client.get(f"/api/system/captcha/audio/{key}.wav")
            assert resp.status_code == 200
            assert resp["Content-Type"] == "audio/wav"
            assert resp["Content-Disposition"] == f'attachment; filename="{key}.wav"'
        finally:
            self._clean(wav_path)

    @override_settings(CAPTCHA_FLITE_PATH=FLITE, CAPTCHA_SOX_PATH=FLITE)
    def test_sox_noise_merge_pipeline(self, api_client):
        """配置 sox 时应执行加噪与混音流水线并返回最终音频。"""
        key = _make_store(challenge="ef")
        wav_path = os.path.join(tempfile.gettempdir(), f"{key}.wav")
        self._clean(wav_path)
        try:
            with mock.patch("captcha.views.subprocess.call", side_effect=self._fake_call, return_value=0) as fake_call:
                resp = api_client.get(f"/api/system/captcha/audio/{key}.wav")
            assert resp.status_code == 200
            assert resp["Content-Type"] == "audio/wav"
            # 三次调用：flite 合成、sox 生成噪声、sox 混音
            assert fake_call.call_count == 3
        finally:
            self._clean(wav_path)

    # ---- 辅助 ----

    @staticmethod
    def _fake_call(args, **kwargs):
        """模拟 flite/sox：为命令行中出现的 wav 输出路径写入占位内容。"""
        for item in args:
            if isinstance(item, str) and item.endswith(".wav") and os.path.isabs(item):
                Path(item).write_bytes(b"RIFFd32d")
        return 0

    @staticmethod
    def _clean(path):
        if os.path.isfile(path):
            os.remove(path)
