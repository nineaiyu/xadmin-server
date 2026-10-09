from typing import Any

from notifications.message import SiteMessageUtil as Client

from .base import BackendBase


class SiteMessage(BackendBase):
    account_field = "id"

    def send_msg(self, users: Any, message: Any, subject: Any, **kwargs: Any) -> None:
        accounts, __, __ = self.get_accounts(users)
        Client.send_msg(subject, message, user_ids=accounts, **kwargs)

    @classmethod
    def is_enable(cls) -> Any:
        return True


backend = SiteMessage
