"""..."""
from movies.libs.alidrive.core import *
from movies.libs.alidrive.request import *
from movies.libs.alidrive.response import *


class Download(Core):
    """..."""

    def get_download_url(self,
                         file_id: str,
                         file_name: str = None,
                         expire_sec: int = 14400,
                         drive_id: str = None,
                         ) -> GetDownloadUrlResponse:
        """
        获取下载链接: 一般在已知 file_id 或 download_url 失效时使用
        :param file_id: [str] 文件 id
        :param file_name: Optional[str] 文件名
        :param expire_sec: Optional[int] 下载链接有效时间, 默认为 4 小时, 这也是允许的最大值
        :param drive_id: Optional[str] 文件所在的网盘 id
        :return: [GetDownloadUrlResponse]

        用法示例:
        >>> from movies.libs.alidrive import Aligo
        >>> ali = Aligo()
        >>> result = ali.get_download_url(file_id='<file_id>')
        >>> print(result)
        """
        body = GetDownloadUrlRequest(
            file_id=file_id,
            drive_id=drive_id,
            file_name=file_name,
            expire_sec=expire_sec,
        )
        return self._core_get_download_url(body)
