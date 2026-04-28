#!/usr/bin/env python
# -*- coding:utf-8 -*-
from rest_framework.routers import SimpleRouter

from announcement.views.admin import AnnouncementAdminViewSet
from announcement.views.user import AnnouncementUserViewSet

app_name = 'announcement'

router = SimpleRouter(False)

router.register('admin', AnnouncementAdminViewSet, basename='announcement-admin')
router.register('user', AnnouncementUserViewSet, basename='announcement-user')

urlpatterns = [
]
urlpatterns += router.urls
