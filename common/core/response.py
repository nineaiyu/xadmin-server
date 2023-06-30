#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : response
# author : ly_13
# date : 6/2/2023

from rest_framework.response import Response


class ApiResponse(Response):
    def __init__(self, code=1000, detail='success', data=None, status=None, headers=None, content_type=None, **kwargs):
        dic = {
            'code': code,
            'detail': detail
        }
        if data is not None:
            dic['data'] = data
        dic.update(kwargs)
        self._data = data
        # 对象来调用对象的绑定方法，会自动传值
        super().__init__(data=dic, status=status, headers=headers, content_type=content_type)

        # 类来调用对象的绑定方法，这个方法就是一个普通函数，有几个参数就要传几个参数
        # Response.__init__(data=dic,status=status,headers=headers,content_type=content_type)
