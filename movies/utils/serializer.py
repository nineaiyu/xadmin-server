#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializer
# author : ly_13
# date : 11/17/2023

import logging

from rest_framework import serializers

from movies.models import AliyunDrive, AliyunFile, FilmInfo, Category, EpisodeInfo, WatchHistory, SwipeInfo, ActorInfo

logger = logging.getLogger(__file__)


class AliyunDriveSerializer(serializers.ModelSerializer):
    class Meta:
        model = AliyunDrive
        fields = ['pk', 'owner', 'user_name', 'nick_name', 'user_id', 'default_drive_id', 'default_sbox_drive_id',
                  'avatar', 'expire_time', 'x_device_id', 'used_size', 'total_size', 'description', 'enable', 'private',
                  'active', 'created_time', 'updated_time']
        read_only_fields = list(
            set([x.name for x in AliyunDrive._meta.fields]) - {"enable", "private", "description"})


class AliyunFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = AliyunFile
        fields = ['pk', 'aliyun_drive', 'name', 'file_id', 'created_time', 'size', 'content_type', 'category',
                  'downloads', 'description', 'used', 'duration', 'is_upload']
        read_only_fields = list(set([x.name for x in AliyunFile._meta.fields]) - {"description"})

    used = serializers.SerializerMethodField()

    def get_used(self, obj):
        return hasattr(obj, 'episodeinfo')


class CategoryListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['value', 'label']
        read_only_fields = list(set([x.name for x in Category._meta.fields]))

    value = serializers.IntegerField(source='id')
    label = serializers.CharField(source='name')


class FilmInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = FilmInfo
        fields = ['pk', 'name', 'title', 'poster', 'category', 'region', 'language', 'subtitle', 'director', 'channel',
                  'starring', 'times', 'views', 'rate', 'description', 'enable', 'created_time', 'updated_time',
                  'category_info', 'release_date', 'region_info', 'language_info', 'channel_info', 'subtitle_info',
                  'director_info', 'introduction', 'current_play_pk', 'episode_count']
        extra_kwargs = {'pk': {'read_only': True}, 'poster': {'read_only': True}}

    category_info = serializers.SerializerMethodField(read_only=True)
    region_info = serializers.SerializerMethodField(read_only=True)
    language_info = serializers.SerializerMethodField(read_only=True)
    channel_info = serializers.SerializerMethodField(read_only=True)
    subtitle_info = serializers.SerializerMethodField(read_only=True)
    director_info = serializers.SerializerMethodField(read_only=True)
    current_play_pk = serializers.SerializerMethodField(read_only=True)
    episode_count = serializers.SerializerMethodField(read_only=True)

    def get_category_info(self, obj):
        return CategoryListSerializer(obj.category, many=True).data

    def get_region_info(self, obj):
        return CategoryListSerializer(obj.region, many=True).data

    def get_language_info(self, obj):
        return CategoryListSerializer(obj.language, many=True).data

    def get_channel_info(self, obj):
        return CategoryListSerializer(obj.channel, many=True).data

    def get_subtitle_info(self, obj):
        return CategoryListSerializer(obj.subtitle, many=True).data

    def get_director_info(self, obj):
        return CategoryListSerializer(obj.director, many=True).data

    def get_current_play_pk(self, obj):
        user = self.context.get('user')
        if user and user.is_authenticated:
            history = obj.watchhistory_set.last()
            if history:
                return history.episode_id
        episode = obj.episodeinfo_set.order_by('rank').first()
        if episode:
            return episode.pk
        return 1

    def get_episode_count(self, obj):
        return obj.episodeinfo_set.count()

    def update(self, instance, validated_data):
        if instance.episodeinfo_set.count() == 0:
            validated_data['enable'] = False
        return super().update(instance, validated_data)

    def create(self, validated_data):
        validated_data['enable'] = False
        return super().create(validated_data)


class EpisodeInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = EpisodeInfo
        fields = ['pk', 'name', 'files', 'enable', 'created_time', 'updated_time', 'file_id', 'film', 'views', "rank"]
        extra_kwargs = {'pk': {'read_only': True}, 'files': {'read_only': True}}
        read_only_fields = ("pk", "files", "rank")

    file_id = serializers.CharField(write_only=True)

    def validate(self, attrs):
        ali_file = AliyunFile.objects.filter(file_id=attrs.pop('file_id')).first()
        attrs['files'] = ali_file
        if not attrs['name'] and ali_file:
            attrs['name'] = ali_file.name
        return attrs

    files = serializers.SerializerMethodField()

    def get_files(self, obj):
        return {'pk': obj.files.pk, 'file_id': obj.files.file_id, 'name': obj.files.name,
                'duration': obj.files.duration, 'size': obj.files.size}


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['name', 'created_time', 'pk', 'description', 'enable', 'count', 'category_type', 'category_display',
                  'rank']

    count = serializers.SerializerMethodField()
    category_display = serializers.CharField(source='get_category_type_display', read_only=True)

    def get_count(self, obj):
        return obj.film_category.count()


class WatchHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = WatchHistory
        fields = ['created_time', 'pk', 'times', 'owner', 'episode', 'updated_time']

    owner = serializers.SerializerMethodField()
    episode = serializers.SerializerMethodField()

    def get_owner(self, obj):
        return {'pk': obj.owner.pk, 'username': obj.owner.username}

    def get_episode(self, obj):
        times = obj.episode.files.duration if obj.episode.files.duration else obj.episode.film.times
        return {'pk': obj.episode.pk, 'name': obj.episode.name, 'film_name': obj.episode.film.name,
                'times': times, 'film_pk': obj.episode.film.pk}


class SwipeInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = SwipeInfo
        fields = ['pk', 'name', 'rank', 'picture', 'enable', 'created_time', 'description', 'route']
        read_only_fields = ['pk', 'picture', 'created_time']


class ActorInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActorInfo
        fields = ['pk', 'name', 'foreign_name', 'enable', 'created_time', 'description', 'sex', 'birthday',
                  'introduction', 'avatar']
        read_only_fields = ['pk', 'avatar', 'created_time']
