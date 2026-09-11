import importlib

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)

client_name_mapper = {}


class BACKEND(models.TextChoices):
    EMAIL = "email", _("Email")
    SITE_MSG = "site_msg", _("Site message")
    SMS = "sms", _("SMS")

    # DINGTALK = 'dingtalk', _('DingTalk')

    @property
    def client(self):
        return client_name_mapper[self]

    def get_account(self, user):
        return self.client.get_account(user)

    @property
    def is_enable(self):
        return self.client.is_enable()

    @classmethod
    def filter_enable_backends(cls, backends):
        """过滤出当前可用的渠道。

        存量订阅数据里可能残留已下线渠道（如 dingtalk）：未知取值只告警跳过，
        不让历史数据打断发布链路；渠道开关检查异常同样跳过该渠道。
        """
        enable_backends = []
        for b in backends:
            try:
                if cls(b).is_enable:
                    enable_backends.append(b)
            except ValueError:
                logger.warning("Unknown notification backend %r in subscription, skip it", b)
            except Exception:
                logger.warning("Check notification backend %s enable state failed, skip it", b, exc_info=True)
        return enable_backends


def load_backend_clients(backend_members=None):
    """按约定加载渠道客户端：`backends/<name>.py` 暴露模块级 `backend`（BackendBase 子类）。

    新增渠道 = 新增一个模块文件，无需修改本文件（消息渲染映射另见
    notifications/notifications.py 的 register_backend_msg）；单个渠道加载失败
    （缺依赖 / 缺 backend 属性）只告警跳过，不阻断通知模块启动。
    加载结果写入 client_name_mapper，供 BACKEND.client 按名取用。
    """
    members = backend_members if backend_members is not None else list(BACKEND)
    for b in members:
        try:
            m = importlib.import_module(f".{b}", __package__)
        except ImportError:
            logger.warning("Import notification backend %s failed, skip it", b, exc_info=True)
            continue
        client = getattr(m, "backend", None)
        if client is None:
            logger.warning("Notification backend module %s has no `backend` attribute, skip it", m.__name__)
            continue
        client_name_mapper[b] = client


load_backend_clients()
