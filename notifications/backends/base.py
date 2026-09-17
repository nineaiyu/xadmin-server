from django.conf import settings

from common.utils import get_logger

logger = get_logger(__name__)

# 未绑定账号用户的可观测上限：日志只列前 N 个用户名，避免全站广播时刷屏
UNBOUND_LOG_LIMIT = 10


def log_unbound_users(backend_name, requirement, unbound_users):
    """记录「未绑定接收账号而被跳过」的用户。

    渠道可达性可观测：排查"为什么没收到"时，先看这里是否过滤掉了未绑定账号的用户。
    必须用 warning——默认 LOG_LEVEL=WARNING，debug/info 在生产日志里不可见（等于没有线索）；
    一次发送只聚合成一行，用户名按上限截断，批量通知不刷屏。
    """
    names = [user.username for user in unbound_users[:UNBOUND_LOG_LIMIT]]
    if len(unbound_users) > UNBOUND_LOG_LIMIT:
        names.append(f"...(+{len(unbound_users) - UNBOUND_LOG_LIMIT})")
    logger.warning(
        "Notification backend %s skip %s user(s) without %s: %s",
        backend_name,
        len(unbound_users),
        requirement,
        ", ".join(names),
    )


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
            log_unbound_users(type(self).__name__, f"{self.account_field} bound", unbound_users)
        return accounts, unbound_users, account_user_mapper

    @classmethod
    def get_account(cls, user):
        return getattr(user, cls.account_field)

    @classmethod
    def is_enable(cls):
        # 渠道开关未在 settings 暴露时按禁用处理，避免发送链路 AttributeError
        return bool(getattr(settings, cls.is_enable_field_in_settings, False))
