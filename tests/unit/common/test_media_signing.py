# -*- coding: utf-8 -*-
"""受保护媒体签名 URL：敏感命名空间无签名 / 过期 / 篡改路径一律拒绝。"""

import time
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import RequestFactory

from common.utils.media import media_serve
from common.utils.media_signing import (
    MEDIA_EXP_PARAM,
    MEDIA_SIG_PARAM,
    sign_media_path,
    sign_media_url,
    verify_media_signature,
)

SENSITIVE_PATH = "file/uploadfile/7/9/report.xlsx"
PUBLIC_PATH = "identity/userinfo/7/9/avatar.png"
CONTENT = b"protected-media"


class _Authenticated:
    is_authenticated = True


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def sensitive_file(media_root):
    target = media_root / SENSITIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(CONTENT)
    return target


@pytest.fixture
def rf():
    factory = RequestFactory(HTTP_USER_AGENT="pytest-agent")

    def _get(path):
        request = factory.get(path)
        request.user = _Authenticated()
        return request

    return _get


def _request_path(path, query=""):
    return f"/media/{path}" + (f"?{query}" if query else "")


class TestSignedUrlVerification:
    def test_unsigned_sensitive_path_is_forbidden(self, rf, media_root, sensitive_file):
        response = media_serve(rf(_request_path(SENSITIVE_PATH)), SENSITIVE_PATH, document_root=str(media_root))
        assert response.status_code == 403

    def test_expired_signature_is_forbidden(self, rf, media_root, sensitive_file):
        signed = sign_media_path(SENSITIVE_PATH, ttl=-10)
        response = media_serve(rf(_request_path(signed)), SENSITIVE_PATH, document_root=str(media_root))
        assert response.status_code == 403

    def test_tampered_path_is_forbidden(self, rf, media_root):
        signed = sign_media_path(SENSITIVE_PATH, ttl=60)
        other = "file/uploadfile/7/9/other.xlsx"
        target = media_root / other
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(CONTENT)
        response = media_serve(rf(_request_path(other, signed.split("?")[1])), other, document_root=str(media_root))
        assert response.status_code == 403

    def test_valid_signature_is_served(self, rf, media_root, sensitive_file):
        signed = sign_media_path(SENSITIVE_PATH, ttl=60)
        response = media_serve(rf(_request_path(signed)), SENSITIVE_PATH, document_root=str(media_root))
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == CONTENT

    def test_public_namespace_stays_login_only(self, rf, media_root):
        target = media_root / PUBLIC_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(CONTENT)
        response = media_serve(rf(_request_path(PUBLIC_PATH)), PUBLIC_PATH, document_root=str(media_root))
        assert response.status_code == 200

    def test_compatibility_switch_disables_signature_requirement(self, rf, media_root, sensitive_file, settings):
        settings.MEDIA_SIGNED_URL_REQUIRED = False
        response = media_serve(rf(_request_path(SENSITIVE_PATH)), SENSITIVE_PATH, document_root=str(media_root))
        assert response.status_code == 200


class TestSignMediaUrl:
    def test_sensitive_media_url_gets_query_signature(self, settings):
        settings.MEDIA_URL = "media/"
        url = sign_media_url("/media/file/uploadfile/7/9/report.xlsx", ttl=60)
        query = parse_qs(urlsplit(url).query)
        assert set(query) == {MEDIA_EXP_PARAM, MEDIA_SIG_PARAM}
        assert int(query[MEDIA_EXP_PARAM][0]) > int(time.time())

    def test_public_media_url_is_unchanged(self, settings):
        settings.MEDIA_URL = "media/"
        url = sign_media_url("/media/identity/userinfo/7/9/avatar.png")
        assert url == "/media/identity/userinfo/7/9/avatar.png"

    def test_external_cdn_url_is_unchanged(self):
        url = sign_media_url("https://cdn.example.com/file/uploadfile/report.xlsx")
        assert url == "https://cdn.example.com/file/uploadfile/report.xlsx"


class TestVerifyMediaSignature:
    def test_requires_path_bound_signature(self):
        signed = sign_media_path(SENSITIVE_PATH, ttl=60)
        query = parse_qs(signed.split("?")[1])
        assert verify_media_signature(SENSITIVE_PATH, query[MEDIA_EXP_PARAM][0], query[MEDIA_SIG_PARAM][0])
        assert not verify_media_signature(
            "file/uploadfile/7/9/other.xlsx", query[MEDIA_EXP_PARAM][0], query[MEDIA_SIG_PARAM][0]
        )
