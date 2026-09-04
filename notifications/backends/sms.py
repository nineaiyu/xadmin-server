from common.sdk.sms import endpoint as sms_endpoint
from .base import BackendBase


class SMS(BackendBase):
    account_field = 'phone'
    is_enable_field_in_settings = 'SMS_ENABLED'

    def __init__(self):
        # 注意：此前直接引用 SMS 会因类名遮蔽 import 而递归实例化自身，
        # 导致 send_msg 调用 self.client.send_sms 时报 AttributeError
        self.client = sms_endpoint.SMS()

    def send_msg(self, users, sign_name: str, template_code: str, template_param: dict):
        accounts, __, __ = self.get_accounts(users)
        if not accounts:
            return
        return self.client.send_sms(accounts, sign_name, template_code, template_param)


backend = SMS
