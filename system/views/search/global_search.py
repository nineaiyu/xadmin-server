#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全局搜索接口（ADR-028，G9）。

URL 权限：菜单权限码 ``retrieve:SystemGlobalSearch``（GET，种子已登记）；
分组级权限门与数据权限门见 system/search.py（逐实体两道门）。
"""

from rest_framework.views import APIView

from common.core.response import ApiResponse
from system.search import KEYWORD_MAX_LENGTH, run_global_search


class GlobalSearchAPIView(APIView):
    """全局搜索：跨实体关键词检索，返回有命中的分组（用户/部门/文件/审批单/操作日志）。"""

    def get(self, request, *args, **kwargs):
        """按 keyword 检索全部可见实体（scope 可限定单个分组）。"""
        keyword = (request.query_params.get("keyword") or "").strip()
        scope = (request.query_params.get("scope") or "").strip()
        groups = run_global_search(request.user, keyword, scope=scope or None)
        return ApiResponse(data={"keyword": keyword[:KEYWORD_MAX_LENGTH], "groups": groups})
