from django.conf import settings

from common.sdk.sms import endpoint as sms_endpoint
from common.utils import get_logger
from .base import BackendBase

logger = get_logger(__name__)


class SMS(BackendBase):
    """短信通知渠道（模板短信）。

    与邮件/站内信不同，短信服务商只接受预审批模板：通知正文以
    「通知模板 + 单变量」（SMS_NOTIFY_TEMPLATE_PARAM_KEY，默认 content）发送。
    渠道开关（SMS_ENABLED）开启但签名/模板未配置时，is_enable 返回 False，
    发送链路自动降级跳过，不影响其他渠道。
    """

    account_field = "phone"
    is_enable_field_in_settings = "SMS_ENABLED"

    def __init__(self):
        # 注意：此前直接引用 SMS 会因类名遮蔽 import 而递归实例化自身，
        # 导致 send_msg 调用 self.client.send_sms 时报 AttributeError
        self.client = sms_endpoint.SMS()

    @classmethod
    def get_notify_template_config(cls):
        """通知短信的签名/模板配置；未配置返回 None（渠道自动降级为不可用）。"""
        sign_name = getattr(settings, "SMS_NOTIFY_SIGN_NAME", "")
        template_code = getattr(settings, "SMS_NOTIFY_TEMPLATE_CODE", "")
        if not (sign_name and template_code):
            return None
        return {
            "sign_name": sign_name,
            "template_code": template_code,
            "template_param": {getattr(settings, "SMS_NOTIFY_TEMPLATE_PARAM_KEY", "content"): ""},
        }

    @classmethod
    def is_enable(cls):
        if not super().is_enable():
            return False
        return cls.get_notify_template_config() is not None

    def send_msg(self, users, message, subject="", **kwargs):
        if not self.is_enable():
            logger.warning("SMS notify template is not configured, skip sms channel")
            return
        accounts, __, __ = self.get_accounts(users)
        if not accounts:
            return
        template_config = self.get_notify_template_config()
        template_param = dict(template_config["template_param"])
        param_key = next(iter(template_param), "content")
        template_param[param_key] = message
        return self.client.send_sms(
            accounts, template_config["sign_name"], template_config["template_code"], template_param
        )


backend = SMS
