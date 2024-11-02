#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : urls
# author : ly_13
# date : 6/6/2023
from rest_framework.routers import SimpleRouter

from demo.views import BookViewSet

app_name = 'demo'

router = SimpleRouter(False)  # 设置为 False ,为了去掉url后面的斜线

router.register('book', BookViewSet, basename='book')

urlpatterns = [
]
urlpatterns += router.urls
