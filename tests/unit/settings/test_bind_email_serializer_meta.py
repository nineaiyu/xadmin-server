# -*- coding: utf-8 -*-
"""绑定邮箱安全开关序列化器字段元数据回归：文案归属与字段语义一一对应。

SecurityBindEmailAuthSerializer 曾出现两类文案漂移：
- 临时令牌与加密两个开关互换了 label / help_text，界面提示与运行时行为相反
  （verify_code.py 中 TEMP_TOKEN 键控制防攻击临时令牌握手，ENCRYPTED 键控制
  提交内容密文解密）；
- 验证码开关的 help_text 复制自"重置密码"页文案。
按字段名锁定各字段 label / help_text 的 msgid 归属，防止再次漂移。
"""

from django.utils.translation import gettext_lazy as _

from settings.serializers.security import SecurityBindEmailAuthSerializer

# 字段名 → (label, help_text)：与 _() 比较做到语言中立（随激活语言一同求值），
# 键与 verify_code.py get_bind_email_config 消费的真实 settings 键一一对应
EXPECTED_TEXTS = {
    "SECURITY_BIND_EMAIL_TEMP_TOKEN_ENABLED": (
        _("Bind email temp token"),
        _("Enable temporary tokens to prevent attacks"),
    ),
    "SECURITY_BIND_EMAIL_ENCRYPTED_ENABLED": (
        _("Bind email encrypted"),
        _("Enable encryption to prevent information leakage"),
    ),
    "SECURITY_BIND_EMAIL_CAPTCHA_ENABLED": (
        _("Bind email captcha"),
        _("Enable captcha to prevent robot bind email"),
    ),
}


def test_bind_email_field_texts_match_field_semantics():
    fields = SecurityBindEmailAuthSerializer().fields
    for name, (label, help_text) in EXPECTED_TEXTS.items():
        assert fields[name].label == label, name
        assert fields[name].help_text == help_text, name
