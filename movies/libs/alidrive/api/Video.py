"""..."""

from movies.libs.alidrive.core import *
from movies.libs.alidrive.request import *
from movies.libs.alidrive.response import *
from movies.libs.alidrive.response import GetVideoPreviewPlayInfoResponse
from movies.libs.alidrive.types.Enum import VideoTemplateID


class Video(Core):
    """..."""

    def get_video_play_info(self, file_id: str, drive_id: str = None) -> GetVideoPlayInfoResponse:
        """
        获取视频播放信息
        :param file_id: [必须] 视频文件ID
        :param drive_id: [可选] 文件所在的网盘盘符ID
        :return: [GetVideoPlayInfoResponse]
        """
        body = GetVideoPlayInfoRequest(file_id=file_id, drive_id=drive_id)
        return self._core_get_video_play_info(body)

    def get_video_preview_play_info(self, file_id: str, template_id: VideoTemplateID = '',
                                    drive_id: str = None,
                                    url_expire_sec: int = 14400) -> GetVideoPreviewPlayInfoResponse:
        """
        获取视频预览播放信息
        :param file_id: [必须] 视频文件ID
        :param url_expire_sec: [可选] url过期时间
        :param template_id: [可选] 视频模板ID
        :param drive_id: [可选] 文件所在的网盘盘符ID
        :return: [GetVideoPreviewPlayInfoResponse]
        """
        body = GetVideoPreviewPlayInfoRequest(file_id=file_id, template_id=template_id, drive_id=drive_id,
                                              url_expire_sec=url_expire_sec)
        return self._core_get_video_preview_play_info(body)
