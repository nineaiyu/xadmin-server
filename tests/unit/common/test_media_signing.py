# -*- coding: utf-8 -*-
"""受保护媒体签名 URL：无签名 / 过期 / 篡改路径 / 他人链接一律拒绝。"""

import hashlib
import hmac
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


class _User(_Authenticated):
    """带 pk 的最小用户桩（签名绑定只消费 pk）。"""

    def __init__(self, pk):
        self.pk = pk


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


class TestUserBoundSignature:
    """签名绑定用户（默认开启）：链接转发给其他登录用户后不可用（fail-closed）。"""

    def test_signature_is_bound_to_signing_user(self):
        signed = sign_media_path(SENSITIVE_PATH, ttl=60, user=_User(1))
        query = parse_qs(signed.split("?")[1])
        assert verify_media_signature(SENSITIVE_PATH, query[MEDIA_EXP_PARAM][0], query[MEDIA_SIG_PARAM][0], _User(1))
        assert not verify_media_signature(
            SENSITIVE_PATH, query[MEDIA_EXP_PARAM][0], query[MEDIA_SIG_PARAM][0], _User(2)
        )
        # 无用户上下文签出的链接（如无请求的内部签发点）：认证后恒不匹配
        assert not verify_media_signature(SENSITIVE_PATH, query[MEDIA_EXP_PARAM][0], query[MEDIA_SIG_PARAM][0])

    def test_bind_disabled_falls_back_to_legacy_two_segment_form(self, settings):
        """关闭开关 = 回退开关：消息回到仅「路径 + 过期」的旧口径，任意用户可验。"""
        settings.SECRET_KEY = "media-signing-test-key"
        settings.MEDIA_SIGNED_URL_BIND_USER = False
        signed = sign_media_path(SENSITIVE_PATH, ttl=60, user=_User(1))
        query = parse_qs(signed.split("?")[1])
        expires = query[MEDIA_EXP_PARAM][0]
        # 与历史两段消息逐字节一致（升级前签发的未过期链接继续可用）
        legacy = hmac.new(
            b"media-signing-test-key",
            f"{SENSITIVE_PATH}\n{expires}".encode(),
            hashlib.sha256,
        ).hexdigest()
        assert query[MEDIA_SIG_PARAM][0] == legacy
        for viewer in (_User(1), _User(2), None):
            assert verify_media_signature(SENSITIVE_PATH, expires, query[MEDIA_SIG_PARAM][0], viewer)

    def test_media_serve_rejects_other_user_link(self, rf, media_root, sensitive_file, settings):
        """端到端：他人签发的链接对当前请求用户 403；本人链接照常下载。"""
        settings.SECRET_KEY = "media-signing-test-key"
        signed = sign_media_path(SENSITIVE_PATH, ttl=60, user=_User(1))
        request = rf(_request_path(signed))
        request.user = _User(2)
        response = media_serve(request, SENSITIVE_PATH, document_root=str(media_root))
        assert response.status_code == 403

        request = rf(_request_path(signed))
        request.user = _User(1)
        response = media_serve(request, SENSITIVE_PATH, document_root=str(media_root))
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == CONTENT
