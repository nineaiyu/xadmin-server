#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : filter
# author : ly_13
# date : 6/2/2023
from rest_framework.filters import BaseFilterBackend


class OwnerUserFilter(BaseFilterBackend):

    def filter_queryset(self, request, queryset, view):
        return queryset.filter(owner_id=request.user)
