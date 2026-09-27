# -*- coding: utf-8 -*-
"""受鉴权媒体服务（/media/）：匿名拒绝、登录态放行、X-Accel 内转配置。

背景：原先 nginx 把 `/media/` 作为静态目录直出（URL 即凭证，匿名可取件），
与「下载走受鉴权端点」的口径矛盾。现统一为「应用鉴权 + 可选 nginx 内部重定向」：
- 匿名请求 403；
- 登录态（Cookie JWT，浏览器同源 img/link 请求）放行；
- 配置 `MEDIA_X_ACCEL_PREFIX` 时返回 `X-Accel-Redirect`（生产 nginx 零拷贝直出），
  DEBUG 下仍由应用进程输出（开发/E2E 直连无 nginx）。
"""

import re
import shutil
from pathlib import Path

import pytest
from django.conf import settings as dj_settings
from django.test import Client, override_settings
from rest_framework_simplejwt.tokens import RefreshToken

pytestmark = pytest.mark.django_db

# 注意：URL 路由在导入期把 MEDIA_ROOT 固定进 document_root，测试不能再 override，
# 直接在测试环境的 MEDIA_ROOT（<项目>/tmp/test_media）下造文件
CASE_DIR = "media_access_case"
MEDIA_URL_PATH = f"/media/{CASE_DIR}/media-access.txt"
CONTENT = b"media-guard-content"


@pytest.fixture
def media_root():
    root = Path(dj_settings.MEDIA_ROOT) / CASE_DIR
    root.mkdir(parents=True, exist_ok=True)
    (root / "media-access.txt").write_bytes(CONTENT)
    yield str(Path(dj_settings.MEDIA_ROOT))
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def media_client(superuser):
    """带 Cookie JWT（X-Token）的客户端：模拟浏览器同源 img/link 请求。"""
    client = Client()
    client.cookies["X-Token"] = str(RefreshToken.for_user(superuser).access_token)
    return client


def body(response) -> bytes:
    return b"".join(response.streaming_content)


class TestMediaAuth:
    def test_anonymous_is_forbidden(self, media_root):
        response = Client().get(MEDIA_URL_PATH)
        assert response.status_code == 403

    def test_authenticated_gets_file(self, media_root, media_client):
        response = media_client.get(MEDIA_URL_PATH)
        assert response.status_code == 200
        assert body(response) == CONTENT

    def test_x_accel_redirect_when_configured(self, media_root, media_client):
        with override_settings(MEDIA_X_ACCEL_PREFIX="/_protected_media"):
            response = media_client.get(MEDIA_URL_PATH)
        assert response.status_code == 200
        assert response["X-Accel-Redirect"] == f"/_protected_media/{CASE_DIR}/media-access.txt"
        assert not response.content

    def test_debug_serves_file_even_with_accel_prefix(self, media_root, media_client):
        """DEBUG（开发/E2E 直连无 nginx）仍由应用输出，避免 X-Accel 头被忽略后空响应。"""
        with override_settings(MEDIA_X_ACCEL_PREFIX="/_protected_media", DEBUG=True):
            response = media_client.get(MEDIA_URL_PATH)
        assert response.status_code == 200
        assert "X-Accel-Redirect" not in response
        assert body(response) == CONTENT

    def test_directory_not_listed(self, media_root, media_client):
        response = media_client.get(f"/media/{CASE_DIR}/")
        assert response.status_code == 404

    def test_missing_file_returns_404(self, media_root, media_client):
        response = media_client.get(f"/media/{CASE_DIR}/not-exists.txt")
        assert response.status_code == 404


class TestMediaNginxPolicySync:
    """nginx 与后端口径同源守护（单仓检出时跳过）。

    - `/media/` 位置不得再有静态 alias 直出（匿名直链已移除）；
    - 受保护内部位置声明 `internal` 且 alias 指向 upload 目录，前缀与
      `MEDIA_X_ACCEL_PREFIX` 的推荐值一致。
    """

    def _read_default_conf(self) -> str:
        path = Path(dj_settings.PROJECT_DIR).parent / "xadmin-web" / "default.conf"
        if not path.exists():
            pytest.skip("单仓检出（缺 xadmin-web/default.conf），跳过 media 口径比对")
        return path.read_text(encoding="utf-8")

    def test_media_location_proxies_to_backend(self):
        text = self._read_default_conf()
        match = re.search(r"location \^~ /media/ \{(.*?)\n    \}", text, re.S)
        assert match, "xadmin-web/default.conf 未找到 /media/ 位置"
        block = match.group(1)
        assert "alias" not in block, "/media/ 不得再静态直出（匿名直链，须经应用鉴权）"
        assert "xadmin-api-conf" in block, "/media/ 应转发到后端做鉴权"

    def test_protected_media_location_is_internal(self):
        text = self._read_default_conf()
        assert "location ^~ /_protected_media/" in text, "缺少受保护媒体的内部位置"
        assert "internal;" in text, "受保护媒体位置必须声明 internal（不可外部寻址）"
        assert "/data/xadmin/server/data/upload/" in text, "受保护媒体位置 alias 未指向 upload 目录"
