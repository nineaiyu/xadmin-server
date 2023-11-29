#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : urls
# author : ly_13
# date : 11/17/2023
from django.urls import include, re_path
from rest_framework.routers import SimpleRouter

from movies.views.alidrive import AliyunDriveView, AliyunDriveQRView
from movies.views.alifile import AliyunFileView
from movies.views.download import M3U8View, DirectlyDownloadView
from movies.views.film import FilmInfoView, EpisodeInfoView, CategoryView, WatchHistoryView

router = SimpleRouter(False)
router.register('drive', AliyunDriveView)
router.register('file', AliyunFileView)
router.register('film', FilmInfoView)
router.register('episode', EpisodeInfoView)
router.register('category', CategoryView)
router.register('history', WatchHistoryView)

urlpatterns = [
    re_path(r"^download/(?P<file_pk>\w+)/(?P<file_id>\w+)/(?P<file_name>\S+)", DirectlyDownloadView.as_view(),
            name="aliyun_file_download"),
    re_path('^qrcode-drive$', AliyunDriveQRView.as_view(), name='qrcode_drive'),
    re_path(r'^m3u8/(?P<file_id>\w+)$', M3U8View.as_view(), name='m3u8'),
    re_path('', include(router.urls))
]
