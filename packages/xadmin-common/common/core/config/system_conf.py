#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 12/15/2023
"""系统配置缓存：系统级配置属性与渲染上下文。

BaseConfCache 按域拆分（文件行数门禁）：文件上传/Office 预览/存储见 conf_upload.py，
安全审批/SCIM/CSP/OAuth 见 conf_security.py，日志保留/导入导出/会话监控见 conf_ops.py；
本模块保留组装（BaseConfCache / MessagePushConfCache / ConfigCache / SysConfig），
维持既有导入面（common.core.config 再导出）不变。
"""

from typing import Any

from common.injection import get_server_config
from common.utils import get_logger

from .base import ConfigCacheBase
from .conf_ops import OpsConfMixin
from .conf_security import SecurityConfMixin
from .conf_upload import UploadConfMixin

logger = get_logger(__name__)


class BaseConfCache(UploadConfMixin, SecurityConfMixin, OpsConfMixin):
    """系统级配置读取（键 → 值）。

    默认值单一来源：全部回读 server 装配的静态配置实例 ``CONFIG``
    （经 common.injection 注入，即 config.yml / 环境变量的值或代码默认值），
    本类不再硬编码任何默认值；``loadjson/systemconfig.json`` 的种子初值须与
    conf.py 一致（守护测试校验）。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class MessagePushConfCache(ConfigCacheBase):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    @property
    def PUSH_MESSAGE_NOTICE(self) -> Any:
        return self.get_value("PUSH_MESSAGE_NOTICE", get_server_config().PUSH_MESSAGE_NOTICE)

    @property
    def PUSH_CHAT_MESSAGE(self) -> Any:
        return self.get_value("PUSH_CHAT_MESSAGE", get_server_config().PUSH_CHAT_MESSAGE)


class ConfigCache(BaseConfCache, MessagePushConfCache):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


SysConfig = ConfigCache()
