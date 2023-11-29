"""..."""
from movies.libs.alidrive import BaseUser, UserConfig
from movies.libs.alidrive.core.BaseAligo import BaseAligo
from movies.libs.alidrive.core.Config import *


class User(BaseAligo):
    """..."""

    def get_user(self, f5: bool = False) -> BaseUser:
        """
        获取用户信息
        :param f5: [可选] 是否强制刷新
        :return: [BaseUser]
        """
        if self._user is None or f5:
            response = self._post(V2_USER_GET)
            # response.status_code == 200 or self._error_log_exit(response)
            # self._user = BaseUser(**response.json())
            self._user = self._result(response, BaseUser)
        return self._user

    def get_user_config(self) -> UserConfig:
        """获取用户配置信息"""
        response = self._post(ADRIVE_V1_USER_CONFIG_GET, body={})
        return self._result(response, UserConfig)
