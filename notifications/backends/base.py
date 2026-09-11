from django.conf import settings

from common.utils import get_logger

logger = get_logger(__name__)


class BackendBase:
    # User 表中的字段
    account_field = None

    # Django setting 中的字段名
    is_enable_field_in_settings = None

    def get_accounts(self, users):
        accounts = []
        unbound_users = []
        account_user_mapper = {}

        for user in users:
            account = getattr(user, self.account_field, None)
            if account:
                account_user_mapper[account] = user
                accounts.append(account)
            else:
                unbound_users.append(user)
        if unbound_users:
            # 渠道可达性可观测：排查"为什么没收到"时，先看这里是否过滤掉了未绑定账号的用户
            logger.debug(
                "Notification backend %s skip %s user(s) without %s bound",
                type(self).__name__,
                len(unbound_users),
                self.account_field,
            )
        return accounts, unbound_users, account_user_mapper

    @classmethod
    def get_account(cls, user):
        return getattr(user, cls.account_field)

    @classmethod
    def is_enable(cls):
        # 渠道开关未在 settings 暴露时按禁用处理，避免发送链路 AttributeError
        return bool(getattr(settings, cls.is_enable_field_in_settings, False))
