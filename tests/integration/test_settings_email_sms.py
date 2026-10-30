# -*- coding: utf-8 -*-
"""设置中心邮件 / 短信连通性测试接口（超管诊断入口）集成测试。

 盲区收口：settings/views/email.py 与 settings/views/sms.py 此前
31% / 48% 覆盖。外部依赖（SMTP / 阿里云短信）全部替换为桩，不发真实请求。
"""

from smtplib import SMTPSenderRefused

import pytest
from django.core import mail
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException

from integrations.sdk.sms.endpoint import BACKENDS
from settings.models import Setting

pytestmark = pytest.mark.django_db

# settings 路由为 SimpleRouter(False)（trailing_slash=False）：URL 一律不带尾斜杠
EMAIL_URL = "/api/settings/email"
SMS_BACKENDS_URL = "/api/settings/sms/backends"
SMS_CONFIG_URL = "/api/settings/sms/config"

EMAIL_PAYLOAD = {
    "EMAIL_HOST": "smtp.test.local",
    "EMAIL_PORT": "465",
    "EMAIL_HOST_USER": "tester@test.local",
    "EMAIL_HOST_PASSWORD": "secret",
    "EMAIL_USE_SSL": True,
    "EMAIL_USE_TLS": False,
    "EMAIL_SUBJECT_PREFIX": "[xadmin] ",
    "EMAIL_RECIPIENT": "to@test.local",
}

SMS_PAYLOAD = {
    "ALIBABA_ACCESS_KEY_ID": "ak-123",
    "ALIBABA_VERIFY_SIGN_NAME": "签名",
    "ALIBABA_VERIFY_TEMPLATE_CODE": "SMS_123",
    "SMS_TEST_PHONE": "13800138000",
}

# 各供应商的专属字段面（write_only 密钥字段不出现在 GET 响应里，不在此列）
BACKEND_EXCLUSIVE_FIELDS = {
    "alibaba": {"ALIBABA_ACCESS_KEY_ID", "ALIBABA_VERIFY_SIGN_NAME", "ALIBABA_VERIFY_TEMPLATE_CODE"},
}


class FakeSMSClient:
    """替换真实阿里云短信客户端：记录入参，不发起网络请求。"""

    instances = []

    def __init__(self, **init_params):
        self.init_params = init_params
        self.send_kwargs = None
        FakeSMSClient.instances.append(self)

    def send_sms(self, phone_numbers, sign_name, template_code, template_param, **kwargs):
        self.send_kwargs = {
            "phone_numbers": phone_numbers,
            "sign_name": sign_name,
            "template_code": template_code,
            "template_param": template_param,
        }


class APIErrorSMSClient(FakeSMSClient):
    def send_sms(self, **kwargs):
        raise APIException(detail={"errmsg": "quota exceeded"})


class UnexpectedSMSClient(FakeSMSClient):
    def send_sms(self, **kwargs):
        raise RuntimeError("network down")


@pytest.fixture(autouse=True)
def _reset_sms_stubs():
    FakeSMSClient.instances = []
    yield
    FakeSMSClient.instances = []


class TestEmailSettingView:
    def test_get_returns_current_settings(self, auth_client):
        resp = auth_client.get(EMAIL_URL)
        assert resp.status_code == 200
        assert resp.data["code"] == 1000

    def test_test_send_success_via_locmem(self, auth_client, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        # 测试邮件主题 = EMAIL_SUBJECT_PREFIX + "Test"（运行时 settings，
        # 非 POST payload）；显式设前缀以锁定「前缀 + Test」拼接不被 or 优先级吞掉
        settings.EMAIL_SUBJECT_PREFIX = "[xadmin] "
        mail.outbox = []

        resp = auth_client.post(EMAIL_URL, EMAIL_PAYLOAD)

        assert resp.status_code == 200
        assert resp.data["code"] == 1000, resp.data
        assert "to@test.local" in resp.data["detail"]
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["to@test.local"]
        # `or` 低于 `+` 的优先级 bug 曾让已设前缀时 subject 只剩前缀
        assert mail.outbox[0].subject == "[xadmin] Test"

    def test_test_send_sender_refused_gbk_decoded(self, auth_client, monkeypatch):
        from settings.views import email as email_view

        def refused(*args, **kwargs):
            raise SMTPSenderRefused(554, "邮箱认证失败".encode("gbk"), "tester@test.local")

        monkeypatch.setattr(email_view, "send_mail", refused)
        resp = auth_client.post(EMAIL_URL, EMAIL_PAYLOAD)

        assert resp.data["code"] == 1001
        # 服务端按 gbk → utf8 逐个尝试解码，中文错误信息可读
        assert resp.data["detail"] == "邮箱认证失败"

    def test_test_send_sender_refused_undecodable_falls_back_to_repr(self, auth_client, monkeypatch):
        from settings.views import email as email_view

        def refused(*args, **kwargs):
            raise SMTPSenderRefused(554, b"\xff\xfe\xfd", "tester@test.local")

        monkeypatch.setattr(email_view, "send_mail", refused)
        resp = auth_client.post(EMAIL_URL, EMAIL_PAYLOAD)

        assert resp.data["code"] == 1001
        assert resp.data["detail"] == str(b"\xff\xfe\xfd")

    def test_test_send_unexpected_error(self, auth_client, monkeypatch):
        from settings.views import email as email_view

        def boom(*args, **kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(email_view, "send_mail", boom)
        resp = auth_client.post(EMAIL_URL, EMAIL_PAYLOAD)

        assert resp.data["code"] == 1002
        # 对外统一文案：网络/协议等原始异常细节不回显，只进服务端日志
        assert resp.data["detail"] == str(_("Email test failed, please check the SMTP configuration"))
        assert "connection refused" not in resp.data["detail"]

    def test_test_send_requires_host_and_user(self, auth_client):
        resp = auth_client.post(EMAIL_URL, {"EMAIL_HOST": ""})
        assert resp.status_code == 400


class TestSmsSettingView:
    def test_backends_lists_choices(self, auth_client):
        resp = auth_client.get(SMS_BACKENDS_URL)
        assert resp.data["code"] == 1000
        values = {item["value"] for item in resp.data["data"]}
        assert "alibaba" in values

    def test_general_endpoint_carries_effective_diagnostic(self, auth_client, settings):
        """总开关诊断挂在「短信设置」页签（/api/settings/sms），不混入服务商页签。"""
        settings.SMS_ENABLED = False
        resp = auth_client.get("/api/settings/sms")

        assert resp.status_code == 200
        item = resp.data["data"]["effective"]["items"][0]
        assert item["enabled"] is False
        assert item["reasons"]

    def test_general_endpoint_reports_missing_credentials_as_blocked(self, auth_client, settings):
        """开关开着但凭据不完整时不能报“已生效”（发不出验证码）。"""
        settings.SMS_ENABLED = True
        settings.SMS_BACKEND = "alibaba"
        settings.ALIBABA_ACCESS_KEY_ID = ""
        settings.ALIBABA_ACCESS_KEY_SECRET = ""
        settings.ALIBABA_VERIFY_SIGN_NAME = ""
        settings.ALIBABA_VERIFY_TEMPLATE_CODE = ""
        resp = auth_client.get("/api/settings/sms")

        item = resp.data["data"]["effective"]["items"][0]
        assert item["enabled"] is False
        assert item["reasons"] == [str(_("SMS provider credentials are incomplete; verify codes may fail to be sent"))]

    def test_provider_endpoint_does_not_carry_general_effective(self, auth_client):
        """阿里云页签只负责服务商凭据，不应显示短信服务总开关的生效诊断。"""
        resp = auth_client.get(f"{SMS_CONFIG_URL}?category=alibaba")

        assert resp.status_code == 200
        assert "effective" not in resp.data["data"]

    @pytest.mark.parametrize("backend", BACKENDS.values)
    def test_config_field_face_for_every_registered_backend(self, auth_client, backend):
        resp = auth_client.get(f"{SMS_CONFIG_URL}?category={backend}")

        assert resp.status_code == 200
        fields = set(resp.data["data"])
        # 任一已注册供应商都收敛到合法字段面：公共测试字段必在、专属字段不缺，
        # 不再错配基础设置序列化器（其字段与短信域对不上）
        assert "SMS_TEST_PHONE" in fields
        assert "SITE_URL" not in fields
        assert BACKEND_EXCLUSIVE_FIELDS.get(backend, set()) <= fields

    def test_config_unknown_category_returns_400(self, auth_client):
        """未知渠道不再落到 provider-not-support 的误导文案，保存期明确 400。"""
        resp = auth_client.post(f"{SMS_CONFIG_URL}?category=nonexistent", SMS_PAYLOAD)

        assert resp.status_code == 400
        assert "nonexistent" in str(resp.data)

    def test_config_without_category_defaults_alibaba(self, auth_client):
        """缺省 category 按 Alibaba 口径校验；缺测试手机号返回受控 1001。"""
        payload = {key: value for key, value in SMS_PAYLOAD.items() if key != "SMS_TEST_PHONE"}
        resp = auth_client.post(SMS_CONFIG_URL, payload)

        assert resp.data["code"] == 1001
        assert resp.data["detail"] == str(_("test_phone is required"))

    def test_config_alibaba_send_success(self, auth_client, settings, monkeypatch):
        monkeypatch.setattr("integrations.sdk.sms.alibaba.client", FakeSMSClient)
        resp = auth_client.post(f"{SMS_CONFIG_URL}?category=alibaba", SMS_PAYLOAD)

        # 成功必须下发默认业务码（前端只认 1000；HTTP 200 会被判为业务失败）
        assert resp.data["code"] == 1000, resp.data
        assert resp.data["detail"] == str(_("Test success"))

        client = FakeSMSClient.instances[-1]
        # 未传 secret 时取配置值为空串，实例化参数仍完整
        assert client.init_params == {"access_key_id": "ak-123", "access_key_secret": ""}
        # PhoneField 校验时已规范为 E.164
        assert client.send_kwargs["phone_numbers"] == ["+8613800138000"]
        # 测试验证码固定为 VERIFY_CODE_LENGTH 位 6，避免消耗真实额度语义
        assert client.send_kwargs["template_param"] == {"code": "6" * settings.VERIFY_CODE_LENGTH}

    def test_config_alibaba_secret_falls_back_to_setting_row(self, auth_client, monkeypatch):
        Setting.objects.create(name="ALIBABA_ACCESS_KEY_SECRET", value='"db-secret"', category="alibaba")
        monkeypatch.setattr("integrations.sdk.sms.alibaba.client", FakeSMSClient)
        resp = auth_client.post(f"{SMS_CONFIG_URL}?category=alibaba", SMS_PAYLOAD)

        assert resp.data["code"] == 1000
        client = FakeSMSClient.instances[-1]
        assert client.init_params["access_key_secret"] == "db-secret"

    def test_config_alibaba_api_error_surfaces_errmsg(self, auth_client, monkeypatch):
        monkeypatch.setattr("integrations.sdk.sms.alibaba.client", APIErrorSMSClient)
        resp = auth_client.post(f"{SMS_CONFIG_URL}?category=alibaba", SMS_PAYLOAD)

        assert resp.data["code"] == 1001
        assert resp.data["detail"] == "quota exceeded"

    def test_config_alibaba_unexpected_error_returns_unified_detail(self, auth_client, monkeypatch):
        monkeypatch.setattr("integrations.sdk.sms.alibaba.client", UnexpectedSMSClient)
        resp = auth_client.post(f"{SMS_CONFIG_URL}?category=alibaba", SMS_PAYLOAD)

        assert resp.data["code"] == 1002
        # 对外统一文案：SDK/网络原始细节不回显，原始异常只进服务端日志
        assert resp.data["detail"] == str(_("SMS test failed, please check the SMS configuration"))
        assert "network down" not in resp.data["detail"]
        # 失败响应外层结构不变（前端据 code 分流）
        assert {"code", "detail", "requestId", "timestamp"} <= set(resp.data)

    def test_partial_update_persists_test_phone(self, auth_client):
        resp = auth_client.patch(f"{SMS_CONFIG_URL}?category=alibaba", SMS_PAYLOAD)
        assert resp.status_code == 200, resp.data

        # post_save 只修正响应表示；入库值即校验后的 E.164 字符串
        setting = Setting.objects.filter(name="SMS_TEST_PHONE").first()
        assert setting is not None
        assert setting.cleaned_value == "+8613800138000"
