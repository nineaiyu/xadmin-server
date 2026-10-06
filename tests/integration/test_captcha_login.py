# -*- coding: utf-8 -*-
"""验证码（captcha）端到端用例：登录强开验证码链路 + 匿名端点限流。

unit 级（生成 / 消费语义 / 渲染分支）之上的端到端守护：
- ``SECURITY_LOGIN_CAPTCHA_ENABLED`` 强开后，登录配置、无码 / 错码 / 正确码 /
  一次性消费的完整登录链路；
- 验证码图片 / 刷新端点的每 IP 固定窗口限流（429），两个端点计数隔离；
- 匿名取图对失效 key 的 410 语义（防爬虫索引）。
"""

import pytest

from captcha.models import CaptchaStore
from captcha.utils import CaptchaAuth

pytestmark = pytest.mark.django_db

LOGIN_URL = "/api/system/login/basic"
CAPTCHA_REFRESH_URL = "/api/system/captcha/refresh/"


def _captcha_url(key):
    return f"/api/system/captcha/image/{key}/"


def _gen_captcha():
    """生成一枚验证码，返回 (key, 明文答案)。答案从 CaptchaStore 反查（测试专用视角）。"""
    data = CaptchaAuth().generate()
    store = CaptchaStore.objects.get(hashkey=data["captcha_key"])
    return data["captcha_key"], store.response


@pytest.fixture
def login_free(settings):
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = False
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


@pytest.fixture
def captcha_on(settings):
    """登录强开验证码，其余登录辅助安全项关闭（聚焦验证码链路本身）。"""
    settings.SECURITY_LOGIN_CAPTCHA_ENABLED = True
    settings.SECURITY_LOGIN_ENCRYPTED_ENABLED = False
    settings.SECURITY_LOGIN_TEMP_TOKEN_ENABLED = False


def _login_payload(key=None, answer=None):
    payload = {"username": "zhangsan", "password": "Test@123456"}
    if key is not None:
        payload["captcha_key"] = key
        payload["captcha_code"] = answer
    return payload


class TestLoginCaptchaEnforced:
    """登录强开验证码：无码 / 错码拒绝，正确码放行，消费一次性。"""

    def test_login_config_reports_captcha_on(self, api_client, captcha_on):
        resp = api_client.get(LOGIN_URL)
        assert resp.data["code"] == 1000
        assert resp.data["data"]["captcha"] is True

    def test_login_without_captcha_rejected(self, api_client, normal_user, captcha_on):
        resp = api_client.post(LOGIN_URL, _login_payload(), format="json")
        assert resp.status_code == 400
        assert "Captcha" in resp.data["detail"] or "验证码" in resp.data["detail"]

    def test_login_with_wrong_code_rejected(self, api_client, normal_user, captcha_on):
        key, _answer = _gen_captcha()
        resp = api_client.post(LOGIN_URL, _login_payload(key, "xxxx"), format="json")
        assert resp.status_code == 400
        normal_user.refresh_from_db()
        assert not normal_user.otp_secret_key  # 未触达任何凭证校验后的写路径

    def test_login_with_valid_captcha_succeeds(self, api_client, normal_user, captcha_on):
        key, answer = _gen_captcha()
        resp = api_client.post(LOGIN_URL, _login_payload(key, answer), format="json")
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["data"]["access"]

    def test_captcha_consumed_once(self, api_client, normal_user, captcha_on):
        """验证码一次性语义：首次登录成功后，同 key 同码的第二次登录被拒。"""
        key, answer = _gen_captcha()
        payload = _login_payload(key, answer)
        resp = api_client.post(LOGIN_URL, payload, format="json")
        assert resp.data["code"] == 1000, resp.data

        resp = api_client.post(LOGIN_URL, payload, format="json")
        assert resp.status_code == 400

    def test_wrong_code_does_not_count_login_failure(self, api_client, normal_user, captcha_on):
        """验证码失败发生在凭证校验之前，不计入账号防爆破锁定计数。"""
        from settings.services import LoginBlockUtil

        for _ in range(3):
            key, _answer = _gen_captcha()
            resp = api_client.post(LOGIN_URL, _login_payload(key, "xxxx"), format="json")
            assert resp.status_code == 400
        assert not LoginBlockUtil(normal_user.username, "127.0.0.1").is_block()

    def test_login_without_captcha_when_disabled(self, api_client, normal_user, login_free):
        """开关关闭时验证码字段可不传，直接放行（对照基线）。"""
        resp = api_client.post(LOGIN_URL, _login_payload(), format="json")
        assert resp.data["code"] == 1000, resp.data


class TestCaptchaEndpointRateLimit:
    """匿名验证码端点每 IP 固定窗口限流：取图 / 刷新都会落 CaptchaStore 行，超窗 429。

    限流阈值经 monkeypatch 收紧到 3 次/窗（生产默认 60），验证的是「端点 → 限流 → 429」
    全链路语义而非具体数值；窗口计数落 Redis 缓存，由 autouse 缓存清理隔离用例。
    """

    LIMIT = 3

    @pytest.fixture(autouse=True)
    def _tighten_limit(self, monkeypatch):
        import captcha.views

        monkeypatch.setattr(captcha.views, "CAPTCHA_IP_LIMIT_PER_MINUTE", self.LIMIT)

    def test_image_rate_limited_after_burst(self, api_client):
        key, _answer = _gen_captcha()
        for _ in range(self.LIMIT):
            resp = api_client.get(_captcha_url(key))
            assert resp.status_code == 200
        resp = api_client.get(_captcha_url(key))
        assert resp.status_code == 429

    def test_refresh_rate_limited_after_burst(self, api_client):
        for _ in range(self.LIMIT):
            resp = api_client.get(CAPTCHA_REFRESH_URL, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            assert resp.status_code == 200
        resp = api_client.get(CAPTCHA_REFRESH_URL, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert resp.status_code == 429

    def test_rate_limit_scopes_isolated(self, api_client):
        """图片端点超窗后，刷新端点计数独立仍放行（scope 按端点分桶）。"""
        key, _answer = _gen_captcha()
        for _ in range(self.LIMIT + 1):
            api_client.get(_captcha_url(key))
        resp = api_client.get(CAPTCHA_REFRESH_URL, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert resp.status_code == 200

    def test_refresh_requires_ajax_header(self, api_client):
        """刷新端点仅服务 AJAX：缺 X-Requested-With 头按 404 隐藏入口。"""
        resp = api_client.get(CAPTCHA_REFRESH_URL)
        assert resp.status_code == 404

    def test_image_missing_store_gone(self, api_client):
        """失效 / 伪造 key 返回 410 Gone，避免爬虫索引过期验证码地址。"""
        resp = api_client.get(_captcha_url("missingkey0"))
        assert resp.status_code == 410
