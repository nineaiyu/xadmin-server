from notifications.message import SiteMessageUtil as Client
from .base import BackendBase


class SiteMessage(BackendBase):
    account_field = 'id'

    def send_msg(self, users, message, subject, **kwargs):
        accounts, __, __ = self.get_accounts(users)
        Client.send_msg(subject, message, user_ids=accounts, **kwargs)

    @classmethod
    def is_enable(cls):
        return True


backend = SiteMessage
