"""..."""
import re

from movies.libs.alidrive.core.BaseAligo import BaseAligo
from movies.libs.alidrive.core.Config import *
from movies.libs.alidrive.request import *
from movies.libs.alidrive.response import *


class Download(BaseAligo):
    """..."""
    _DOWNLOAD_CHUNK_SIZE = 8388608  # 8 MB

    def _core_get_download_url(self, body: GetDownloadUrlRequest) -> GetDownloadUrlResponse:
        """..."""
        response = self._post(V2_FILE_GET_DOWNLOAD_URL, body=body)
        return self._result(response, GetDownloadUrlResponse)

    @staticmethod
    def _del_special_symbol(s: str) -> str:
        """删除Windows文件名中不允许的字符"""
        return re.sub(r'[\\/:*?"<>|]', '_', s)
