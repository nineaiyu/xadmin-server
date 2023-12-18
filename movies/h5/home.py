#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : home
# author : ly_13
# date : 11/30/2023
from hashlib import md5

from django.db.models import Q
from django_filters import rest_framework as filters
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.generics import get_object_or_404
from rest_framework.views import APIView

from common.base.magic import cache_response
from common.core.filter import OwnerUserFilter
from common.core.modelset import OnlyListModelSet
from common.core.response import ApiResponse
from movies.h5.serializer import H5FilmInfoSerializer, H5WatchHistorySerializer, H5ActorInfoSerializer
from movies.models import FilmInfo, SwipeInfo, Category, WatchHistory, EpisodeInfo, ActorInfo
from movies.utils.serializer import SwipeInfoSerializer, CategoryListSerializer, \
    AliyunFileSerializer, FilmInfoSerializer, EpisodeInfoSerializer, ActorInfoSerializer
from movies.utils.storage import get_video_preview, get_download_url
from movies.views.film import WatchHistoryView

release_date = {
    0: {'value': '', 'label': '年份', 'exp': ''},
    1: {'value': 1, 'label': '2023', 'exp': {'release_date__year': 2023}},
    2: {'value': 2, 'label': '2022', 'exp': {'release_date__year': 2022}},
    3: {'value': 3, 'label': '2021', 'exp': {'release_date__year': 2021}},
    4: {'value': 4, 'label': '2020', 'exp': {'release_date__year': 2020}},
    5: {'value': 5, 'label': '10年代', 'exp': {'release_date__range': ['2010-01-01', '2019-12-31']}},
    6: {'value': 6, 'label': '00年代', 'exp': {'release_date__range': ['2000-01-01', '2009-12-31']}},
    7: {'value': 7, 'label': '90年代', 'exp': {'release_date__range': ['1990-01-01', '1999-12-31']}},
    8: {'value': 8, 'label': '80年代', 'exp': {'release_date__range': ['1980-01-01', '1989-12-31']}},
    9: {'value': 9, 'label': '更早', 'exp': {'release_date__lt': '1980-01-01'}},
}


class HomeView(APIView):
    permission_classes = []
    authentication_classes = []

    @cache_response(timeout=600)
    def get(self, request):
        film_queryset = FilmInfo.objects.filter(enable=True)
        film_result = []
        if film_queryset.count():
            film_result.append({
                'title': '最近更新',
                'data': H5FilmInfoSerializer(film_queryset.order_by('-created_time').all()[:9], many=True).data
            })

        for obj in Category.get_channel_category():
            film_data = film_queryset.filter(channel=obj).order_by('-views')
            if film_data.count() > 0:
                film_result.append({
                    'title': f"热门{obj.name}",
                    'data': H5FilmInfoSerializer(film_data.all()[:9], many=True).data
                })

        swipe_data = SwipeInfoSerializer(SwipeInfo.objects.filter(enable=True).order_by('rank'), many=True).data

        return ApiResponse(film=film_result, swipe=swipe_data)


class FilmFilter(filters.FilterSet):
    actor = filters.NumberFilter(field_name='actor', method='actor_filter')
    channel = filters.NumberFilter(field_name='channel')
    region = filters.NumberFilter(field_name='region')
    category = filters.NumberFilter(field_name='category')
    release_date = filters.NumberFilter(field_name='release_date', method='release_date_filter')
    name = filters.CharFilter(field_name="name", method='name_filter')

    def name_filter(self, queryset, name, value):
        if value:
            return queryset.filter(
                Q(name__icontains=value) | Q(title__icontains=value) | Q(director__name__icontains=value) | Q(
                    starring__name__icontains=value)).distinct()
        return queryset

    def actor_filter(self, queryset, name, value):
        if value:
            return queryset.filter(Q(director__id=value) | Q(starring__id=value)).distinct()
        return queryset

    def release_date_filter(self, queryset, name, value):
        exp = release_date[value].get('exp')
        if exp and isinstance(exp, dict):
            return queryset.filter(**exp)
        return queryset

    class Meta:
        model = FilmInfo
        fields = ['id']


class H5FilmView(OnlyListModelSet):
    permission_classes = []
    queryset = FilmInfo.objects.filter(enable=True).all()
    serializer_class = H5FilmInfoSerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ['created_time', 'rate', 'views', 'release_date']
    filterset_class = FilmFilter

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_｛{kwargs.get('pk', 0)}｝_{md5(request.META['QUERY_STRING'].encode('utf-8')).hexdigest()}"

    @cache_response(timeout=600, key_func='get_cache_key')
    def list(self, request, *args, **kwargs):
        data = super().list(request, *args, **kwargs).data
        return ApiResponse(**data)

    @action(methods=['get'], detail=True)
    @cache_response(timeout=600, key_func='get_cache_key')
    def recommend(self, request, *args, **kwargs):
        instance = self.get_object()
        queryset = self.queryset.filter(category__in=instance.category.all()).exclude(pk=instance.pk).order_by(
            '-created_time').distinct()[:9]
        serializer = self.get_serializer(queryset, many=True)
        return ApiResponse(data={'results': serializer.data})

    @action(methods=['get'], detail=True)
    def current(self, request, *args, **kwargs):
        current = 0
        instance = self.get_object()
        if request.user and request.user.is_authenticated:
            history = instance.watchhistory_set.last()
            if history:
                current = history.episode_id
        if not current:
            episode = instance.episodeinfo_set.order_by('rank').first()
            if episode:
                current = episode.pk
        return ApiResponse(current=current)


class H5FilmFilterView(APIView):
    permission_classes = []
    authentication_classes = []

    @cache_response(timeout=600)
    def get(self, request):
        channel = CategoryListSerializer(Category.get_channel_category(), many=True).data
        channel.insert(0, {'value': '', 'label': '频道'})
        region = CategoryListSerializer(Category.get_region_category(), many=True).data
        region.insert(0, {'value': '', 'label': '地区'})
        category = CategoryListSerializer(Category.get_video_category(), many=True).data
        category.insert(0, {'value': '', 'label': '类型'})

        ordering = [
            {'value': '-views', 'label': '播放次数'},
            {'value': '-rate', 'label': '评分'},
            {'value': '-release_date', 'label': '发布时间'},
        ]

        category_result = [
            {'key': 'channel', 'result': channel},
            {'key': 'region', 'result': region},
            {'key': 'category', 'result': category},
            {'key': 'release_date', 'result': release_date.values()},
            {'key': 'ordering', 'result': ordering},
        ]
        return ApiResponse(category=category_result)


class H5FilmDetailView(APIView):
    permission_classes = []

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{kwargs.get('pk')}"

    @cache_response(timeout=600, key_func='get_cache_key')
    def get(self, request, pk):
        film_obj = FilmInfo.objects.filter(pk=pk).first()
        if film_obj:
            film_info = FilmInfoSerializer(film_obj).data
            episode_info = EpisodeInfoSerializer(EpisodeInfo.objects.filter(film=film_obj).order_by('rank').all(),
                                                 many=True).data
            director = H5ActorInfoSerializer(film_obj.director, many=True).data
            starring = H5ActorInfoSerializer(film_obj.starring, many=True).data
            return ApiResponse(film=film_info, episode=episode_info, director=director, starring=starring)
        return ApiResponse()


class H5FilmPreviewView(APIView):
    permission_classes = []

    def get_object(self):
        queryset = EpisodeInfo.objects.all()
        obj = get_object_or_404(queryset, pk=self.kwargs.get('pk'))
        return obj

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{kwargs.get('pk')}"

    @cache_response(timeout=18, key_func='get_cache_key')
    def get(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = AliyunFileSerializer(instance.files)
        data = serializer.data
        data['preview_url'] = get_video_preview(instance.files)
        obj = WatchHistory.objects.filter(episode=instance).first()
        if request.user.is_authenticated and obj:
            data['times'] = obj.times
        return ApiResponse(data=data)

    def post(self, request, *args, **kwargs):
        instance = self.get_object()
        download_url = get_download_url(instance.files)
        if download_url:
            instance.files.downloads += 1
            instance.files.save(update_fields=['downloads'])
            return ApiResponse(**download_url)
        return ApiResponse(code=1002, detail='获取下载连接失败')


class H5WatchHistoryFilter(filters.FilterSet):
    name = filters.CharFilter(field_name='film__name', lookup_expr='icontains')

    class Meta:
        model = WatchHistory
        fields = ['id']


class H5WatchHistoryView(WatchHistoryView):
    permission_classes = []
    queryset = WatchHistory.objects.all()
    serializer_class = H5WatchHistorySerializer

    filter_backends = [filters.DjangoFilterBackend, OrderingFilter, OwnerUserFilter]
    ordering_fields = ['created_time', 'times']
    filterset_class = H5WatchHistoryFilter


class H5FilmActorDetailView(APIView):
    permission_classes = []

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{kwargs.get('pk')}"

    @cache_response(timeout=600, key_func='get_cache_key')
    def get(self, request, pk):
        actor_obj = ActorInfo.objects.filter(pk=pk).first()
        if actor_obj:
            actor = ActorInfoSerializer(actor_obj).data
            return ApiResponse(data={'results': [actor]})
        return ApiResponse()
