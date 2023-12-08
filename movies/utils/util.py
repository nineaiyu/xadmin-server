#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : util
# author : ly_13
# date : 12/8/2023
import time

from movies.libs.alidrive import BaseFile
from movies.models import AliyunFile
from movies.tasks import delay_sync_drive_size


def get_duration(complete: BaseFile):
    duration = complete.video_media_metadata.duration
    if not duration:
        video_media_video_stream = complete.video_media_metadata.video_media_video_stream
        if video_media_video_stream:
            duration = video_media_video_stream[0].duration
    if not duration:
        duration = 0
    return duration


def save_file_info(complete: BaseFile, user_obj, drive_obj, ali_obj, refresh=False, is_upload=True):
    fields = ['name', 'file_id', 'drive_id', 'size', 'content_type', 'content_hash', 'crc64_hash', 'category',
              'duration']
    defaults = {'is_upload': is_upload}
    for f in fields:
        if hasattr(complete, f):
            defaults[f] = getattr(complete, f)
    defaults['duration'] = get_duration(complete)
    if not refresh and not defaults['duration'] and complete.category == 'video':
        time.sleep(2)
        complete = ali_obj.get_file(complete.file_id, complete.drive_id)
        defaults['duration'] = get_duration(complete)
    obj, created = AliyunFile.objects.update_or_create(
        owner=user_obj,
        aliyun_drive=drive_obj,
        file_id=complete.file_id,
        defaults=defaults
    )
    if created:
        delay_sync_drive_size(drive_obj.pk)
    return obj
