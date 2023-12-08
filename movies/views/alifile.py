#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : alifile
# author : ly_13
# date : 11/17/2023
import json
import logging
import time

from django.db.models import F
from django_filters import rest_framework as filters
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.base.utils import AesBaseCrypt
from common.cache.storage import DownloadUrlCache, UploadPartInfoCache
from common.core.filter import OwnerUserFilter
from common.core.modelset import BaseModelSet
from common.core.response import ApiResponse
from common.utils.token import generate_alphanumeric_token_of_length
from movies.config import MOVIES_STORAGE_PREFIX
from movies.models import AliyunFile, AliyunDrive, WatchHistory
from movies.utils.serializer import AliyunFileSerializer
from movies.utils.storage import get_aliyun_drive, batch_get_download_url, batch_delete_file, get_video_preview, \
    get_download_url
from movies.utils.util import save_file_info

logger = logging.getLogger(__file__)


def check_sid(request, sid):
    try:
        res = AesBaseCrypt().get_decrypt_uid(sid)
        data = json.loads(res)
        n_time = time.time()
        if data['username'] == request.user.username and data['expire'] > n_time:
            return True
    except Exception as e:
        logger.warning(f'check sid failed. load data exception {e}')
    return False


def get_aliyun_drive_obj(request):
    file_info = request.data.get('file_info')
    if file_info and check_sid(request, file_info.get('sid', '')):
        file_name = file_info.get("file_name", generate_alphanumeric_token_of_length(32))
        file_info['file_name'] = f'{MOVIES_STORAGE_PREFIX}/{request.user.username}/{file_name}'
        drive_queryset = AliyunDrive.objects.filter(active=True, enable=True,
                                                    total_size__gte=F('used_size') + file_info.get('file_size', 0),
                                                    access_token__isnull=False)

        drive_obj = drive_queryset.filter(owner=request.user).order_by('created_time').first()
        if not drive_obj:
            drive_obj = drive_queryset.filter(private=False).order_by('created_time').first()
            if not drive_obj:
                raise Exception('暂无可用存储')
                # return file_info, False, None
        return file_info, get_aliyun_drive(drive_obj), drive_obj
    return file_info, False, None


class AliyunFileFilter(filters.FilterSet):
    min_size = filters.NumberFilter(field_name="size", lookup_expr='gte')
    max_size = filters.NumberFilter(field_name="size", lookup_expr='lte')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    file_id = filters.CharFilter(field_name='file_id')
    pk = filters.NumberFilter(field_name='id')
    drive_id = filters.NumberFilter(field_name="aliyun_drive_id")
    used = filters.BooleanFilter(method="used_filter")
    is_upload = filters.BooleanFilter(field_name='is_upload')

    def used_filter(self, queryset, name, value):
        return queryset.exclude(episodeinfo__isnull=value)

    class Meta:
        model = AliyunFile
        fields = ['name']


class AliyunFileView(BaseModelSet):
    queryset = AliyunFile.objects.all()
    serializer_class = AliyunFileSerializer

    filter_backends = [OwnerUserFilter, filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['size', 'created_time', 'downloads']
    filterset_class = AliyunFileFilter

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.is_upload:
            ali_obj = get_aliyun_drive(instance.aliyun_drive)
            result = ali_obj.move_file_to_trash(instance.file_id)
            logger.debug(f'{instance.aliyun_drive} move {instance} to trash.result:{result}')
        DownloadUrlCache(instance.drive_id, instance.file_id).del_storage_cache()
        self.perform_destroy(instance)
        return ApiResponse()

    def create(self, request, *args, **kwargs):
        return ApiResponse(code=1001, detail='添加失败')

    @action(methods=['delete'], detail=False, url_path='many-delete')
    def many_delete(self, request, *args, **kwargs):
        pks = request.query_params.get('pks', None)
        if not pks:
            return ApiResponse(code=1003, detail="数据异常，批量操作id不存在")
        batch_delete_file(self.queryset.filter(pk__in=json.loads(pks), is_upload=True))
        self.queryset.filter(pk__in=json.loads(pks)).delete()
        return ApiResponse(detail=f"批量操作成功")

    @action(methods=['get'], detail=True)
    def preview(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = AliyunFileSerializer(instance)
        data = serializer.data
        data['preview_url'] = get_video_preview(instance)
        obj = WatchHistory.objects.filter(owner=request.user, episode__files=instance).first()
        if obj:
            data['times'] = obj.times
        return ApiResponse(data=data)

    @action(methods=['get'], detail=False, url_path='many-download')
    def many_download(self, request, *args, **kwargs):
        pks = request.query_params.get('pks', None)
        if not pks:
            return ApiResponse(code=1003, detail="数据异常，批量操作id不存在")
        return ApiResponse(data=batch_get_download_url(self.queryset.filter(pk__in=json.loads(pks))))

    @action(methods=['get'], detail=True, url_path='download-url')
    def download_url(self, request, *args, **kwargs):
        instance = self.get_object()
        download_url = get_download_url(instance)
        if download_url:
            instance.downloads += 1
            instance.save(update_fields=['downloads'])
            return ApiResponse(**download_url)
        return ApiResponse(code=1002, detail='获取下载连接失败')

    @action(methods=['post'], detail=False)
    def auth_sid(self, request, *args, **kwargs):
        n_time = time.time()
        sid = AesBaseCrypt().set_encrypt_uid(json.dumps({'username': request.user.username, 'expire': n_time + 1800}))
        return ApiResponse(data={'sid': sid})

    @action(methods=['post'], detail=False)
    def pre_hash(self, request, *args, **kwargs):
        file_info, ali_obj, _ = get_aliyun_drive_obj(request)
        part_info, check_status = ali_obj.pre_hash_check(file_info)
        logger.debug(f'{file_info.get("file_name")} pre_hash check {part_info}')
        data = {
            'check_status': check_status,
            'md5_token': ali_obj.get_md5_token(),
            'upload_extra': ali_obj.get_upload_extra(),
        }
        if not check_status:
            data['part_info_list'] = ali_obj.reget_upload_part_url(part_info)
        return ApiResponse(**data)

    @action(methods=['post'], detail=False)
    def content_hash(self, request, *args, **kwargs):
        file_info, ali_obj, drive_obj = get_aliyun_drive_obj(request)
        part_info, check_status = ali_obj.content_hash_check(file_info)
        logger.debug(f'{file_info.get("file_name")} pre_hash check {part_info}')
        data = {'check_status': check_status, 'upload_extra': ali_obj.get_upload_extra()}
        if not check_status:
            data['part_info_list'] = ali_obj.reget_upload_part_url(part_info)
        else:
            time.sleep(1)  # 延时获取数据，防止数据异常
            complete = ali_obj.get_file(part_info.file_id, part_info.drive_id)
            obj = save_file_info(complete, request.user, drive_obj, ali_obj)
            UploadPartInfoCache(file_info.get('sid')).del_storage_cache()
            data['file_id'] = complete.file_id
            data['pk'] = obj.pk
        return ApiResponse(**data)

    @action(methods=['post'], detail=False)
    def upload_complete(self, request, *args, **kwargs):
        file_info, ali_obj, drive_obj = get_aliyun_drive_obj(request)
        complete, check_status = ali_obj.upload_complete(file_info)
        logger.debug(f'{file_info.get("file_name")} pre_hash check {complete}')
        if complete and check_status:
            obj = save_file_info(complete, request.user, drive_obj, ali_obj)
            UploadPartInfoCache(file_info.get('sid')).del_storage_cache()
            return ApiResponse(**{'check_status': check_status, 'file_id': complete.file_id, 'pk': obj.pk})
        return ApiResponse(detail='上传失败')

    @action(methods=['post'], detail=True)
    def sync(self, request, *args, **kwargs):
        instance = self.get_object()
        ali_obj = get_aliyun_drive(instance.aliyun_drive)
        complete = ali_obj.get_file(instance.file_id, instance.drive_id)
        save_file_info(complete, instance.owner, instance.aliyun_drive, ali_obj)
        return ApiResponse()
