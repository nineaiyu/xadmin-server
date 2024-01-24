#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializer
# author : ly_13
# date : 11/17/2023

import logging

from rest_framework import serializers

from common.core.serializers import BaseModelSerializer
from movies.models import AliyunDrive, AliyunFile, FilmInfo, Category, EpisodeInfo, WatchHistory, SwipeInfo, ActorInfo
from system.utils.serializer import UserInfoSerializer

logger = logging.getLogger(__file__)


class AliyunDriveSerializer(BaseModelSerializer):
    class Meta:
        model = AliyunDrive
        fields = ['pk', 'owner', 'user_name', 'nick_name', 'user_id', 'default_drive_id', 'default_sbox_drive_id',
                  'avatar', 'expire_time', 'x_device_id', 'used_size', 'total_size', 'description', 'enable', 'private',
                  'active', 'created_time', 'updated_time']
        read_only_fields = list(
            set([x.name for x in AliyunDrive._meta.fields]) - {"enable", "private", "description"})


class AliyunFileSerializer(BaseModelSerializer):
    class Meta:
        model = AliyunFile
        fields = ['pk', 'aliyun_drive', 'name', 'file_id', 'created_time', 'size', 'content_type', 'category',
                  'downloads', 'description', 'used', 'duration', 'is_upload']
        read_only_fields = list(set([x.name for x in AliyunFile._meta.fields]) - {"description"})

    used = serializers.SerializerMethodField()

    def get_used(self, obj):
        return hasattr(obj, 'episodeinfo')


class CategoryListSerializer(BaseModelSerializer):
    class Meta:
        model = Category
        fields = ['value', 'label']
        read_only_fields = list(set([x.name for x in Category._meta.fields]))

    value = serializers.IntegerField(source='id')
    label = serializers.CharField(source='name')


class FilmInfoSerializer(BaseModelSerializer):
    class Meta:
        model = FilmInfo
        fields = ['pk', 'name', 'title', 'poster', 'category', 'region', 'language', 'channel', 'running',
                  'starring', 'times', 'views', 'rate', 'description', 'enable', 'created_time', 'updated_time',
                  'category_info', 'release_date', 'region_info', 'language_info', 'channel_info', 'douban',
                  'introduction', 'episode_count']
        extra_kwargs = {'pk': {'read_only': True}, 'poster': {'read_only': True}}

    category_info = CategoryListSerializer(fields=['value', 'label'], many=True, read_only=True, source='category')
    region_info = CategoryListSerializer(fields=['value', 'label'], many=True, read_only=True, source='region')
    language_info = CategoryListSerializer(fields=['value', 'label'], many=True, read_only=True, source='language')
    channel_info = CategoryListSerializer(fields=['value', 'label'], many=True, read_only=True, source='channel')
    episode_count = serializers.SerializerMethodField(read_only=True)

    def get_episode_count(self, obj):
        return obj.episodeinfo_set.count()

    def update(self, instance, validated_data):
        if instance.episodeinfo_set.count() == 0:
            validated_data['enable'] = False
        return super().update(instance, validated_data)

    def create(self, validated_data):
        validated_data['enable'] = False
        return super().create(validated_data)


class EpisodeInfoSerializer(BaseModelSerializer):
    class Meta:
        model = EpisodeInfo
        fields = ['pk', 'name', 'files', 'enable', 'created_time', 'updated_time', 'file_id', 'film', 'views', "rank"]
        extra_kwargs = {'pk': {'read_only': True}, 'files': {'read_only': True}}
        read_only_fields = ("pk", "files", "rank")

    file_id = serializers.CharField(write_only=True)
    files = AliyunFileSerializer(fields=['pk', 'file_id', 'name', 'duration', 'size'], read_only=True, source='files')

    def validate(self, attrs):
        ali_file = AliyunFile.objects.filter(file_id=attrs.pop('file_id')).first()
        attrs['files'] = ali_file
        if not attrs['name'] and ali_file:
            attrs['name'] = ali_file.name
        return attrs



class CategorySerializer(BaseModelSerializer):
    class Meta:
        model = Category
        fields = ['name', 'created_time', 'pk', 'description', 'enable', 'count', 'category_type', 'category_display',
                  'rank']

    count = serializers.SerializerMethodField()
    category_display = serializers.CharField(source='get_category_type_display', read_only=True)

    def get_count(self, obj):
        return obj.film_category.count()


class WatchHistorySerializer(BaseModelSerializer):
    class Meta:
        model = WatchHistory
        fields = ['created_time', 'pk', 'times', 'owner', 'episode', 'updated_time']

    owner = UserInfoSerializer(fields=['pk', 'username'], read_only=True)
    episode = serializers.SerializerMethodField()


    def get_episode(self, obj):
        times = obj.episode.files.duration if obj.episode.files.duration else obj.episode.film.times
        return {'pk': obj.episode.pk, 'name': obj.episode.name, 'film_name': obj.episode.film.name,
                'times': times, 'film_pk': obj.episode.film.pk}


class SwipeInfoSerializer(BaseModelSerializer):
    class Meta:
        model = SwipeInfo
        fields = ['pk', 'name', 'rank', 'picture', 'enable', 'created_time', 'description', 'route']
        read_only_fields = ['pk', 'picture', 'created_time']


class ActorInfoSerializer(BaseModelSerializer):
    class Meta:
        model = ActorInfo
        fields = ['pk', 'name', 'foreign_name', 'enable', 'created_time', 'description', 'sex', 'birthday',
                  'introduction', 'avatar', 'birthplace', 'profession']
        read_only_fields = ['pk', 'avatar', 'created_time']
