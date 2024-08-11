#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : routers
# author : ly_13
# date : 7/31/2024
from rest_framework.routers import SimpleRouter, Route, DynamicRoute


class NoDetailRouter(SimpleRouter):
    routes = [
        # List route.
        Route(
            url=r'^{prefix}{trailing_slash}$',
            mapping={
                'post': 'create',
                'get': 'retrieve',
                'put': 'update',
                'patch': 'partial_update',
                'delete': 'destroy'
            },
            name='{basename}-detail',
            detail=False,
            initkwargs={'suffix': 'Instance'}
        ),
        # Dynamically generated list routes. Generated using
        # @action(detail=False) decorator on methods of the viewset.
        DynamicRoute(
            url=r'^{prefix}/{url_path}{trailing_slash}$',
            name='{basename}-{url_name}',
            detail=False,
            initkwargs={}
        ),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
