#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : base
# author : ly_13
# date : 8/6/2024
from common.utils import get_logger

logger = get_logger(__name__)


class BaseSMSClient:
    """
    短信终端的基类
    """

    SIGN_AND_TMPL_SETTING_FIELD_PREFIX: str

    @classmethod
    def new_from_settings(cls):
        raise NotImplementedError

    def send_sms(self, phone_numbers: list, sign_name: str, template_code: str, template_param: dict, **kwargs):
        raise NotImplementedError

    @staticmethod
    def need_pre_check():
        return True
