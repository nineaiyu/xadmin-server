#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : pagination
# author : ly_13
# date : 6/16/2023
# -*- coding: utf-8 -*-


from collections import OrderedDict

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class PageNumber(PageNumberPagination):
    page_size = 20  # 每页显示多少条
    page_size_query_param = 'size'  # URL中每页显示条数的参数
    page_query_param = 'page'  # URL中页码的参数
    max_page_size = 100  # 返回最大数据条数

    def get_paginated_response(self, data):
        return Response(OrderedDict([
            ('total', self.page.paginator.count),
            # ('next', self.get_next_link()),
            # ('previous', self.get_previous_link()),
            ('results', data)
        ]))


class MenuPageNumber(PageNumber):
    max_page_size = 500  # 返回最大数据条数
