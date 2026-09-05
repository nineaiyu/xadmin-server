#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""响应缓存 Mixin：详情 / 列表缓存 key 规则与失效方法。

配合 common.base.magic.cache_response 使用。拆分自 modelset.py（T2.1）。
"""

import json
from hashlib import md5

from common.base.magic import cache_response


class CacheDetailResponseMixin(object):
    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f"{view_instance.__class__.__name__}_{view_method.__name__}"
        return f"{func_name}_{request.user.pk}"

    @classmethod
    def invalid_cache(cls, pk, methods=None):
        if methods is None:
            methods = ["retrieve", "get"]
        for method in methods:
            cache_response.invalid_cache(f"{cls.__name__}_{method}_{pk}")


class CacheListResponseMixin(object):
    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f"{view_instance.__class__.__name__}_{view_method.__name__}"
        return f"{func_name}_{request.user.pk}_{md5(json.dumps(request.query_params, sort_keys=True).encode('utf-8')).hexdigest()}"

    @classmethod
    def invalid_cache(cls, pk, methods=None):
        if methods is None:
            methods = ["list"]
        for method in methods:
            cache_response.invalid_cache(f"{cls.__name__}_{method}_{pk}")
