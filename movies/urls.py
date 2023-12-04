#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : urls
# author : ly_13
# date : 11/17/2023
from django.urls import include, re_path
from rest_framework.routers import SimpleRouter

from movies.h5.home import HomeView, H5FilmView, H5FilmFilterView, H5FilmDetailView, H5FilmPreviewView, \
    H5WatchHistoryView, H5FilmActorDetailView
from movies.views.alidrive import AliyunDriveView, AliyunDriveQRView
from movies.views.alifile import AliyunFileView
from movies.views.download import M3U8View, DirectlyDownloadView
from movies.views.film import FilmInfoView, EpisodeInfoView, CategoryView, WatchHistoryView, SwipeInfoView, \
    ActorInfoView

router = SimpleRouter(False)
router.register('drive', AliyunDriveView)
router.register('file', AliyunFileView)
router.register('film', FilmInfoView)
router.register('episode', EpisodeInfoView)
router.register('category', CategoryView)
router.register('history', WatchHistoryView)
router.register('actor', ActorInfoView)
router.register('swipe', SwipeInfoView)

router.register('h5/film', H5FilmView)
router.register('h5/history', H5WatchHistoryView)

urlpatterns = [
    re_path(r"^download/(?P<file_pk>\w+)/(?P<file_id>\w+)/(?P<file_name>\S+)", DirectlyDownloadView.as_view(),
            name="aliyun_file_download"),
    re_path('^qrcode-drive$', AliyunDriveQRView.as_view(), name='qrcode_drive'),
    re_path(r'^m3u8/(?P<file_id>\w+)$', M3U8View.as_view(), name='m3u8'),
    re_path(r'^h5/home$', HomeView.as_view(), name='h5_home'),
    re_path(r'^h5/filter$', H5FilmFilterView.as_view(), name='h5_filter'),
    re_path(r'^h5/detail/(?P<pk>\w+)$', H5FilmDetailView.as_view(), name='h5_detail'),
    re_path(r'^h5/preview/(?P<pk>\w+)$', H5FilmPreviewView.as_view(), name='h5_preview'),
    re_path(r'^h5/actor/(?P<pk>\w+)$', H5FilmActorDetailView.as_view(), name='h5_actor'),

    re_path('', include(router.urls))
]
