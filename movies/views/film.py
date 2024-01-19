#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : film
# author : ly_13
# date : 11/20/2023
import json
import logging

from django.db.models import Q
from django_filters import rest_framework as filters
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter

from common.base.utils import get_choices_dict
from common.core.modelset import BaseModelSet, UploadFileAction, RankAction
from common.core.response import ApiResponse
from movies.models import FilmInfo, Category, EpisodeInfo, WatchHistory, SwipeInfo, ActorInfo
from movies.tasks import sync_douban_movie
from movies.utils.douban.search import search_from_douban
from movies.utils.serializer import FilmInfoSerializer, CategorySerializer, EpisodeInfoSerializer, \
    CategoryListSerializer, WatchHistorySerializer, SwipeInfoSerializer, ActorInfoSerializer

logger = logging.getLogger(__file__)


class FilmFilter(filters.FilterSet):
    language = filters.NumberFilter(field_name='language')
    channel = filters.NumberFilter(field_name='channel')
    region = filters.NumberFilter(field_name='region')
    starring = filters.CharFilter(field_name='starring__name', lookup_expr='icontains')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')
    enable = filters.BooleanFilter(field_name='enable')
    min_release_date = filters.DateFilter(field_name="release_date", lookup_expr='gte')
    max_release_date = filters.DateFilter(field_name="release_date", lookup_expr='lte')
    name = filters.CharFilter(field_name="name", method='name_filter')
    categories = filters.CharFilter(field_name="categories", method='categories_filter')

    def name_filter(self, queryset, name, value):
        if value:
            return queryset.filter(Q(name__icontains=value) | Q(title__icontains=value)).distinct()
        return queryset

    def categories_filter(self, queryset, name, value):
        category = json.loads(value)
        if category:
            return queryset.filter(category__in=json.loads(value))
        return queryset

    class Meta:
        model = FilmInfo
        fields = ['id']


class FilmInfoView(BaseModelSet, UploadFileAction):
    FILE_UPLOAD_FIELD = 'poster'
    queryset = FilmInfo.objects.all().distinct()
    serializer_class = FilmInfoSerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['views', 'created_time', 'rate', 'times']
    filterset_class = FilmFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        category_result = {
            'channel': CategoryListSerializer(Category.get_channel_category(), many=True).data,
            'region': CategoryListSerializer(Category.get_region_category(), many=True).data,
            'category': CategoryListSerializer(Category.get_video_category(), many=True).data,
            'language': CategoryListSerializer(Category.get_language_category(), many=True).data,
        }
        return ApiResponse(**data, **category_result)

    @action(methods=['get'], detail=False)
    def search_douban(self, request, *args, **kwargs):
        key = request.query_params.get('key')
        results = search_from_douban(key)
        return ApiResponse(data={'results': results})

    @action(methods=['post'], detail=False)
    def add_douban(self, request, *args, **kwargs):
        movie_url = request.data.get('movie')
        # get_film_info(movie_url.split('subject/')[-1].replace('/',''))
        c_task = sync_douban_movie.apply_async(args=(str(movie_url),))
        logger.info(f'{movie_url} delay exec {c_task}')
        return ApiResponse()


class EpisodeInfoFilter(filters.FilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    film_id = filters.NumberFilter(field_name='film')
    enable = filters.BooleanFilter(field_name='enable')

    class Meta:
        model = EpisodeInfo
        fields = ['id']


class EpisodeInfoView(BaseModelSet, RankAction):
    queryset = EpisodeInfo.objects.all().distinct()
    serializer_class = EpisodeInfoSerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['rank', 'created_time']
    filterset_class = EpisodeInfoFilter


class CategoryFilter(filters.FilterSet):
    name = filters.CharFilter(field_name='name', lookup_expr='icontains')
    category_type = filters.CharFilter(field_name='category_type')
    enable = filters.BooleanFilter(field_name='enable')

    class Meta:
        model = Category
        fields = ['id']


class CategoryView(BaseModelSet, RankAction):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['created_time', 'rank']
    filterset_class = CategoryFilter

    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data, choices_dict=get_choices_dict(Category.category_type_choices))


class WatchHistoryFilter(filters.FilterSet):
    name = filters.CharFilter(field_name='episode__name', lookup_expr='icontains')
    owner = filters.NumberFilter(field_name='owner__id')

    class Meta:
        model = WatchHistory
        fields = ['id']


class WatchHistoryView(BaseModelSet):
    queryset = WatchHistory.objects.all()
    serializer_class = WatchHistorySerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['created_time', 'times']
    filterset_class = WatchHistoryFilter

    def create(self, request, *args, **kwargs):
        return ApiResponse(detail="禁止操作")

    def update(self, request, *args, **kwargs):
        return ApiResponse(detail="禁止操作")

    @action(methods=['post'], detail=False)
    def times(self, request, *args, **kwargs):
        times = request.data.get('times')
        file_id = request.data.get('file_id')
        episode = EpisodeInfo.objects.filter(files_id=file_id).first()
        if times and episode:
            self.queryset.update_or_create(defaults={'times': times, 'episode': episode}, owner=request.user,
                                           film=episode.film)
        return ApiResponse()

    @action(methods=['post'], detail=False)
    def clean(self, request, *args, **kwargs):
        self.queryset.delete()
        return ApiResponse()


class SwipeInfoFilter(filters.FilterSet):
    name = filters.CharFilter(field_name="name", lookup_expr='icontains')
    enable = filters.BooleanFilter(field_name='enable')
    description = filters.CharFilter(field_name='description', lookup_expr='icontains')

    class Meta:
        model = SwipeInfo
        fields = ['id']


class SwipeInfoView(BaseModelSet, UploadFileAction, RankAction):
    FILE_UPLOAD_FIELD = 'picture'
    queryset = SwipeInfo.objects.all().distinct()
    serializer_class = SwipeInfoSerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['created_time', 'rank']
    filterset_class = SwipeInfoFilter


class ActorInfoFilter(filters.FilterSet):
    enable = filters.BooleanFilter(field_name='enable')
    min_birthday = filters.DateFilter(field_name="birthday", lookup_expr='gte')
    max_birthday = filters.DateFilter(field_name="birthday", lookup_expr='lte')
    introduction = filters.CharFilter(field_name='introduction', lookup_expr='icontains')
    name = filters.CharFilter(field_name="name", method='name_filter')
    pk = filters.NumberFilter(field_name="id")
    pks = filters.CharFilter(field_name="pks", method='pks_filter')

    def pks_filter(self, queryset, name, value):
        category = json.loads(value)
        if category:
            return queryset.filter(pk__in=list(set(json.loads(value))))
        return queryset

    def name_filter(self, queryset, name, value):
        if value:
            return queryset.filter(Q(name__icontains=value) | Q(foreign_name__icontains=value)).distinct()
        return queryset

    class Meta:
        model = ActorInfo
        fields = ['id']


class ActorInfoView(BaseModelSet, UploadFileAction):
    FILE_UPLOAD_FIELD = 'avatar'
    queryset = ActorInfo.objects.all()
    serializer_class = ActorInfoSerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['created_time']
    filterset_class = ActorInfoFilter
