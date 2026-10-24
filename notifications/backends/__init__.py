import importlib
from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger
from notifications.backends.specs import (
    CHANNEL_SPECS as CHANNEL_SPECS,
)
from notifications.backends.specs import (
    SPEC_BY_CODE as SPEC_BY_CODE,
)
from notifications.backends.specs import (
    ChannelSpec as ChannelSpec,
)
from notifications.backends.specs import (
    all_channel_specs as all_channel_specs,  # noqa: F401 消费侧统一入口（内置 + 运行期登记）
)

logger = get_logger(__name__)

client_name_mapper: dict[str, str] = {}


class BACKEND(models.TextChoices):
    EMAIL = "email", _("Email")
    SITE_MSG = "site_msg", _("Site message")
    SMS = "sms", _("SMS")
    # 企业 IM 渠道：收件账号来自对应 flavor 的 OAuth 绑定（见 backends/im_base.py）
    DINGTALK = "dingtalk", _("DingTalk")
    WECOM = "wecom", _("WeCom")
    FEISHU = "feishu", _("FeiShu")

    @property
    def client(self) -> Any:
        return client_name_mapper[self]

    def get_account(self, user: Any) -> Any:
        return self.client.get_account(user)

    @property
    def is_enable(self) -> Any:
        return self.client.is_enable()

    @classmethod
    def filter_enable_backends(cls, backends: Any) -> Any:
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


def load_backend_clients(backend_members: Any = None) -> None:
    """按渠道注册表加载客户端：``backends/<module>.py`` 暴露模块级 ``backend``。

    新增渠道 = ``specs.py`` 加一条 ``ChannelSpec`` + 新增模块文件；消息渲染方法、
    HTML 净化名单、客户端加载均从注册表派生。未登记 spec 的历史渠道仍按枚举值
    作为模块名加载（保持兼容）；单个渠道加载失败只告警跳过，不阻断通知模块启动。
    """
    members = backend_members if backend_members is not None else list(BACKEND)
    for b in members:
        spec = SPEC_BY_CODE.get(str(b))
        module_name = spec.module if spec else str(b)
        try:
            m = importlib.import_module(f".{module_name}", __package__)
        except ImportError:
            logger.warning("Import notification backend %s failed, skip it", module_name, exc_info=True)
            continue
        client = getattr(m, "backend", None)
        if client is None:
            logger.warning("Notification backend module %s has no `backend` attribute, skip it", m.__name__)
            continue
        if spec:
            _validate_spec(spec, client, m.__name__)
        client_name_mapper[b] = client


def _validate_spec(spec: ChannelSpec, client: Any, module_name: str) -> None:
    """注册表与渠道客户端类声明漂移时告警（守护测试会把漂移变成红灯）。"""
    account_field = getattr(client, "account_field", "")
    enable_setting = getattr(client, "is_enable_field_in_settings", "")
    if account_field != spec.account_field or enable_setting != spec.enable_setting:
        logger.warning(
            "Notification channel spec drift for %s (%s): spec(account_field=%r, enable_setting=%r) "
            "!= client(account_field=%r, enable_setting=%r)",
            spec.code,
            module_name,
            spec.account_field,
            spec.enable_setting,
            account_field,
            enable_setting,
        )


load_backend_clients()
