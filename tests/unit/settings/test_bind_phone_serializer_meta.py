# -*- coding: utf-8 -*-
"""绑定手机安全开关序列化器字段元数据回归：文案归属与字段语义一一对应。

SecurityBindEmailAuthSerializer 曾出现文案复制漂移（验证码开关的 help_text
带成"重置密码"页文案、临时令牌与加密开关互换 label / help_text），绑定手机
页签为同族复制源，按字段名锁定各字段 label / help_text 的 msgid 归属，
防止再次漂移。
"""

from django.utils.translation import gettext_lazy as _

from settings.serializers.security import SecurityBindPhoneAuthSerializer

# 字段名 → (label, help_text)：与 _() 比较做到语言中立（随激活语言一同求值），
# 键与 identity/views/auth/verify_code.py get_bind_phone_config 消费的真实
# settings 键一一对应
EXPECTED_TEXTS = {
    "SECURITY_BIND_PHONE_ACCESS_ENABLED": (
        _("Bind phone enable"),
        _("Enable bind phone for user"),
    ),
    "SECURITY_BIND_PHONE_CAPTCHA_ENABLED": (
        _("Bind phone captcha"),
        _("Enable captcha to prevent robot bind phone"),
    ),
    "SECURITY_BIND_PHONE_TEMP_TOKEN_ENABLED": (
        _("Bind phone temp token"),
        _("Enable temporary tokens to prevent attacks"),
    ),
    "SECURITY_BIND_PHONE_ENCRYPTED_ENABLED": (
        _("Bind phone encrypted"),
        _("Enable encryption to prevent information leakage"),
    ),
}


def test_bind_phone_field_texts_match_field_semantics():
    fields = SecurityBindPhoneAuthSerializer().fields
    for name, (label, help_text) in EXPECTED_TEXTS.items():
        assert fields[name].label == label, name
        assert fields[name].help_text == help_text, name
