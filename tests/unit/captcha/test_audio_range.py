# -*- coding: utf-8 -*-
"""音频 Range 响应：200 / 206 / 416 与临时文件清理。"""

import os
import tempfile

import pytest
from django.test import RequestFactory

from captcha.http import audio_file_response

pytestmark = pytest.mark.django_db


def _wav() -> str:
    handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    handle.write(b"RIFF0123456789")
    handle.close()
    return handle.name


class TestAudioRange:
    def test_full_response(self):
        path = _wav()
        response = audio_file_response(RequestFactory().get("/audio"), path, "a.wav")
        assert response.status_code == 200
        assert response["Accept-Ranges"] == "bytes"
        assert response["Content-Disposition"] == 'attachment; filename="a.wav"'
        response.close()
        assert not os.path.exists(path)

    def test_partial_response(self):
        path = _wav()
        request = RequestFactory().get("/audio", HTTP_RANGE="bytes=0-3")
        response = audio_file_response(request, path, "a.wav")
        assert response.status_code == 206
        assert response["Content-Range"] == "bytes 0-3/14"
        assert response.content == b"RIFF"
        assert not os.path.exists(path)

    def test_suffix_range(self):
        path = _wav()
        request = RequestFactory().get("/audio", HTTP_RANGE="bytes=-4")
        response = audio_file_response(request, path, "a.wav")
        assert response.status_code == 206
        assert response.content == b"6789"

    def test_invalid_range(self):
        path = _wav()
        request = RequestFactory().get("/audio", HTTP_RANGE="bytes=99-100")
        response = audio_file_response(request, path, "a.wav")
        assert response.status_code == 416
        assert response["Content-Range"] == "bytes */14"
