#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : storage
# author : ly_13
# date : 11/17/2023
import datetime
import logging
import time
from typing import List
from urllib.parse import parse_qs

from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from common.base.magic import MagicCacheData
from common.cache.storage import DownloadUrlCache, RedisCacheBase
from common.utils.token import make_token
from movies.libs.alidrive import Aligo
from movies.libs.alidrive.types.VideoPreviewPlayInfo import LiveTranscodingTask
from movies.models import AliyunDrive, AliyunFile
from server.settings import CACHE_KEY_TEMPLATE

logger = logging.getLogger(__file__)


class DriveQrCache(RedisCacheBase):
    def __init__(self, locker_key):
        self.cache_key = f"{CACHE_KEY_TEMPLATE.get('drive_qrcode_key')}_{locker_key}"
        super().__init__(self.cache_key)


def quoted(string):
    return '"%s"' % string


class StreamInfo(object):
    bandwidth = None
    program_id = None
    resolution = None
    codecs = None
    name = None

    def __init__(self, **kwargs):
        self.bandwidth = kwargs.get("bandwidth")
        self.program_id = kwargs.get("program_id")
        self.resolution = kwargs.get("resolution")
        self.codecs = kwargs.get("codecs")
        self.name = kwargs.get("name")

    def __str__(self):
        stream_inf = []
        if self.program_id is not None:
            stream_inf.append('PROGRAM-ID=%d' % self.program_id)
        if self.bandwidth is not None:
            stream_inf.append('BANDWIDTH=%d' % self.bandwidth)
        if self.resolution is not None:
            stream_inf.append('RESOLUTION=' + self.resolution)
        if self.codecs is not None:
            stream_inf.append('CODECS=' + quoted(self.codecs))

        if self.name is not None:
            stream_inf.append('NAME=' + self.name)
        return ",".join(stream_inf)


def format_m3u8_data(preview_play_info: List[LiveTranscodingTask]):
    start_str = '#EXTM3U'
    for preview in preview_play_info:
        if preview.status == 'finished':
            if not preview.url:
                continue
            if preview.template_id == 'SD':
                stream_inf = StreamInfo(bandwidth=836280, program_id=1, codecs="mp4a.40.2,avc1.64001f",
                                        name=preview.template_name,
                                        resolution=f"{preview.template_width}x{preview.template_height}")
            elif preview.template_id == 'HD':
                stream_inf = StreamInfo(bandwidth=2149280, program_id=1, codecs="mp4a.40.2,avc1.64001f",
                                        name=preview.template_name,
                                        resolution=f"{preview.template_width}x{preview.template_height}")
            elif preview.template_id == 'FHD':
                stream_inf = StreamInfo(bandwidth=6221600, program_id=1, codecs="mp4a.40.2,avc1.640028",
                                        name=preview.template_name,
                                        resolution=f"{preview.template_width}x{preview.template_height}")
            else:
                stream_inf = StreamInfo(bandwidth=460560, program_id=1, codecs="mp4a.40.5,avc1.420016",
                                        name=preview.template_name,
                                        resolution=f"{preview.template_width}x{preview.template_height}")

            start_str += '\n#EXT-X-STREAM-INF:' + str(stream_inf) + '\n' + preview.url
    return start_str


def get_now_time():
    default_timezone = timezone.get_default_timezone()
    return timezone.make_aware(datetime.datetime.now(), default_timezone)


def cache_time(*args, **kwargs):
    drive_obj = args[0]
    if drive_obj.expire_time > get_now_time():
        return (drive_obj.expire_time - get_now_time()).seconds
    return 1


@MagicCacheData.make_cache(timeout=24 * 60 * 60, key_func=lambda *args: args[0].file_id)
def check_file_punish(file_obj: AliyunFile):
    drive_obj = AliyunDrive.objects.filter(active=True, enable=True, access_token__isnull=False,
                                           pk=file_obj.aliyun_drive.pk).first()
    if drive_obj:
        ali_obj: Aligo = get_aliyun_drive(drive_obj)
        result = ali_obj.get_file(file_obj.file_id, drive_id=file_obj.drive_id)
        return result.punish_flag != 2
    return False


@MagicCacheData.make_cache(timeout=5, key_func=lambda *args: args[0].user_id, timeout_func=cache_time)
def get_aliyun_drive(drive_obj: AliyunDrive) -> Aligo:
    drive_obj = AliyunDrive.objects.filter(pk=drive_obj.pk, active=True).first()
    if drive_obj:
        if drive_obj.expire_time > get_now_time():
            return Aligo(drive_obj)
        else:
            return Aligo(drive_obj, refresh_token=drive_obj.refresh_token)


@MagicCacheData.make_cache(timeout=3600, key_func=lambda *args: f"{args[0].file_id}")
def get_video_m3u8(file_obj: AliyunFile, template_id='FHD|HD|SD|LD', url_expire_sec=14400):
    drive_obj = AliyunDrive.objects.filter(active=True, enable=True, access_token__isnull=False,
                                           pk=file_obj.aliyun_drive.pk).first()
    if drive_obj:
        ali_obj: Aligo = get_aliyun_drive(drive_obj)
        result = ali_obj.get_video_preview_play_info(file_id=file_obj.file_id, drive_id=file_obj.drive_id,
                                                     template_id=template_id, url_expire_sec=url_expire_sec)
        try:
            return format_m3u8_data(result.video_preview_play_info.live_transcoding_task_list[::-1])
        except Exception:
            logger.warning(result)
            return None


def make_m3u8_token(file_obj):
    return make_token(key=file_obj.file_id, time_limit=20, force_new=True, prefix='m3u8')


def get_video_preview(file_obj: AliyunFile):
    if not file_obj:
        return ""
    if not check_file_punish(file_obj):
        return ""
    token = make_m3u8_token(file_obj)
    return f'{reverse("m3u8", kwargs={"file_id": file_obj.file_id})}?token={token}'


def get_download_url(file_obj: AliyunFile, download=False) -> dict:
    if not file_obj:
        return {}
    download_cache = DownloadUrlCache(file_obj.drive_id, file_obj.file_id)
    cache_url = download_cache.get_storage_cache()
    if cache_url:
        return {'file_id': file_obj.file_id, 'download_url': cache_url}
    else:
        drive_obj = AliyunDrive.objects.filter(active=True, enable=True, access_token__isnull=False,
                                               pk=file_obj.aliyun_drive.pk).first()
        if drive_obj:
            ali_obj = get_aliyun_drive(drive_obj)
            if download:
                result = ali_obj.get_download_url(file_id=file_obj.file_id, drive_id=file_obj.drive_id)
                expired = datetime.datetime.strptime(result.expiration, '%Y-%m-%dT%H:%M:%S.%fZ')
                expired_second = (expired - datetime.datetime.utcnow()).seconds
                download_url = result.url
            else:
                result = ali_obj.get_file(file_id=file_obj.file_id, drive_id=file_obj.drive_id)
                download_url = result.download_url
                parse_data = parse_qs(download_url)
                expired = parse_data.get('x-oss-expires')
                if expired and len(expired) == 1:
                    expired_second = int(expired[0]) - time.time()
                else:
                    return {}
            download_cache.set_storage_cache(download_url, timeout=expired_second - 300)
            return {'file_id': file_obj.file_id, 'download_url': download_url}
    return {}


def batch_get_download_url(file_obj_list: [AliyunFile]) -> list:
    cache_result = []
    drive_obj_dict = {}

    for file_obj in file_obj_list:
        download_cache = DownloadUrlCache(file_obj.drive_id, file_obj.file_id)
        cache_url = download_cache.get_storage_cache()
        if cache_url:
            cache_result.append({'file_id': file_obj.file_id, 'download_url': cache_url})
        else:
            drive_user_id = file_obj.aliyun_drive.user_id
            drive_obj = drive_obj_dict.get(drive_user_id)
            if not drive_obj:
                drive_obj_dict[drive_user_id] = [file_obj.file_id]
            else:
                drive_obj_dict[drive_user_id].append(file_obj.file_id)

    for drive_user_id, file_info_list in drive_obj_dict.items():
        drive_obj = AliyunDrive.objects.filter(user_id=drive_user_id, active=True, enable=True,
                                               access_token__isnull=False).first()
        if drive_obj:
            ali_obj = get_aliyun_drive(drive_obj)
            result_list = ali_obj.batch_get_files(file_info_list)
            for result in result_list:
                download_url = result.download_url
                parse_data = parse_qs(download_url)
                expired = parse_data.get('x-oss-expires')
                if expired and len(expired) == 1:
                    expired_second = int(expired[0]) - time.time()
                    download_cache = DownloadUrlCache(result.drive_id, result.file_id)
                    download_cache.set_storage_cache(download_url, timeout=expired_second - 300)
                    cache_result.append({'file_id': result.file_id, 'download_url': download_url})

    return cache_result


def batch_delete_file(file_obj_list: [AliyunFile]) -> bool:
    drive_obj_dict = {}
    for file_obj in file_obj_list:
        drive_user_id = file_obj.aliyun_drive.user_id
        drive_obj = drive_obj_dict.get(drive_user_id)
        if not drive_obj:
            drive_obj_dict[drive_user_id] = [file_obj.file_id]
        else:
            drive_obj_dict[drive_user_id].append(file_obj.file_id)

    for drive_user_id, file_info_list in drive_obj_dict.items():
        drive_obj = AliyunDrive.objects.filter(user_id=drive_user_id, active=True, enable=True,
                                               access_token__isnull=False).first()
        if drive_obj:
            ali_obj = get_aliyun_drive(drive_obj)
            ali_obj.batch_move_to_trash(file_info_list)
    return True


def save_views(instance):
    episode = instance.episodeinfo
    episode.views = F('views') + 1
    episode.save(update_fields=['views'])

    film = episode.film
    film.views = F('views') + 1
    film.save(update_fields=['views'])

def aliyun_sign(aliyun):
    # 获取签到列表
    sign_in_list = aliyun.sign_in_list()
    logger.info('本月签到次数: %d', sign_in_list.result.signInCount)

    # 签到
    for i in sign_in_list.result.signInLogs:
        if i.isReward:
            continue
        if i.status == 'normal':
            sign_in_reward = aliyun.sign_in_reward(i.day)
            logger.info('签到成功: %s', sign_in_reward.result.notice)