#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : serializer
# author : ly_13
# date : 11/30/2023

import logging

from rest_framework import serializers

from movies.models import FilmInfo, WatchHistory, ActorShip
from movies.utils.serializer import FilmInfoSerializer, ActorInfoSerializer

logger = logging.getLogger(__file__)


class H5FilmInfoSerializer(FilmInfoSerializer):
    class Meta:
        model = FilmInfo
        fields = ['pk', 'name', 'poster', 'rate', 'category_info', 'release_date', 'channel_info']
        read_only_fields = list(set([x.name for x in FilmInfo._meta.fields]))


class H5WatchHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = WatchHistory
        fields = ['created_time', 'pk', 'times', 'episode', 'updated_time', 'film']

    episode = serializers.SerializerMethodField()
    film = serializers.SerializerMethodField()

    def get_episode(self, obj):
        times = obj.episode.files.duration if obj.episode.files.duration else obj.episode.film.times
        return {'pk': obj.episode.pk, 'name': obj.episode.name, 'times': times}

    def get_film(self, obj):
        return H5FilmInfoSerializer(obj.episode.film).data


class H5ActorWhoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActorShip
        fields = ['pk', 'name', 'foreign_name', 'avatar', 'who']

    name = serializers.CharField(source='actor.name', read_only=True)
    foreign_name = serializers.CharField(source='actor.foreign_name', read_only=True)
    avatar = serializers.CharField(source='actor.avatar.url', read_only=True)


class H5ActorInfoSerializer(H5ActorWhoSerializer):
    actor = ActorInfoSerializer()

    class Meta:
        model = ActorShip
        fields = ['pk', 'who', 'actor']
