#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : base
# author : ly_13
# date : 8/6/2024
from typing import Any

from common.utils import get_logger

logger = get_logger(__name__)


class BaseSMSClient:
    """
    短信终端的基类
    """

    SIGN_AND_TMPL_SETTING_FIELD_PREFIX: str

    @classmethod
    def new_from_settings(cls) -> Any:
        raise NotImplementedError

    def send_sms(
        self,
        phone_numbers: list[str],
        sign_name: str,
        template_code: str,
        template_param: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        raise NotImplementedError

    @staticmethod
    def need_pre_check() -> bool:
        return True
