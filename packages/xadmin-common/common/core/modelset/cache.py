#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""响应缓存 Mixin：详情 / 列表缓存 key 规则与失效方法。

配合 common.decorators.cache_response 使用。拆分自 modelset.py。
"""

import json
from hashlib import md5
from typing import Any

from common.decorators import cache_response


class CacheDetailResponseMixin:
    def get_cache_key(self, view_instance: Any, view_method: Any, request: Any, args: Any, kwargs: Any) -> str:
        func_name = f"{view_instance.__class__.__name__}_{view_method.__name__}"
        return f"{func_name}_{request.user.pk}"

    @classmethod
    def invalid_cache(cls, pk: Any, methods: Any = None) -> None:
        if methods is None:
            methods = ["retrieve", "get"]
        for method in methods:
            cache_response.invalid_cache(f"{cls.__name__}_{method}_{pk}")


class CacheListResponseMixin:
    def get_cache_key(self, view_instance: Any, view_method: Any, request: Any, args: Any, kwargs: Any) -> str:
        func_name = f"{view_instance.__class__.__name__}_{view_method.__name__}"
        return f"{func_name}_{request.user.pk}_{md5(json.dumps(request.query_params, sort_keys=True).encode('utf-8')).hexdigest()}"

    @classmethod
    def invalid_cache(cls, pk: Any, methods: Any = None) -> None:
        """按用户 pk 失效列表缓存。

        注意：实际缓存键为 ``类名_方法_用户pk_查询参数摘要``（见 get_cache_key），
        本方法拼出的键**不含查询摘要、删不到实际键**——仅对无失效调用点、
        依赖 TTL 自愈的场景保持兼容。新视图若需主动失效列表缓存，应改用
        版本号键方案（失效 = 版本号自增），不要依赖本方法。
        """
        if methods is None:
            methods = ["list"]
        for method in methods:
            cache_response.invalid_cache(f"{cls.__name__}_{method}_{pk}")
