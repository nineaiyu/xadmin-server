"""Aligo class"""
import logging
from typing import Dict

from movies.models import AliyunDrive
from .Create import Create
from .Download import Download
from .File import File
from .Recyclebin import Recyclebin
from .Video import Video


class Aligo(
    Create,
    Download,
    File,
    Recyclebin,
    Video
):
    """阿里云盘"""

    def __init__(
            self,
            drive_obj: AliyunDrive,
            refresh_token: str = None,
            level: int = logging.DEBUG,
            proxies: Dict = None,
    ):
        """
        Aligo
        :param drive_obj: 阿里云盘授权对象
        :param refresh_token:
        :param level: (可选) 控制控制台输出
        :param proxies: (可选) 自定义代理 [proxies={"https":"localhost:10809"}],支持 http 和 socks5（具体参考requests库的用法）

        """

        super().__init__(
            drive_obj,
            refresh_token,
            level,
            proxies,
        )
