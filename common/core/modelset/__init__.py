#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通用 ViewSet 组合模块（T2.1 由单文件 modelset.py 拆分而来）。

按职责拆分为组合模块（组合优先于继承），每个模块 ≤300 行；
对外导入路径与拆分前完全一致，业务视图无需改动：

- base.py           BaseViewSet（查询集优化 / action 级 serializer / 导出绕过分页）
- crud.py           Create / Detail / List / Destroy / Update 五个 CRUD Action
- batch.py          RankAction / BatchDestroyAction（排序、批量删除）
- metadata.py       ChoicesAction / SearchFieldsAction / SearchColumnsAction（元数据接口）
- input_types.py    字段 input_type 推断辅助（get_format_intput_type 等）
- import_export.py  OnlyExportDataAction / ImportExportDataAction（含 Celery 异步导入分发）
- upload.py         UploadFileAction（单文件上传，按需混入）
- cache.py          CacheDetailResponseMixin / CacheListResponseMixin（响应缓存 key/失效）
- viewsets.py       预组合 ViewSet：BaseModelSet / ListDeleteModelSet / OnlyListModelSet /
                    DetailUpdateModelSet / NoDetailModelSet
"""

from common.core.modelset.base import BaseViewSet
from common.core.modelset.batch import BatchDestroyAction, RankAction
from common.core.modelset.cache import CacheDetailResponseMixin, CacheListResponseMixin
from common.core.modelset.crud import CreateAction, DestroyAction, DetailAction, ListAction, UpdateAction
from common.core.modelset.import_export import ImportExportDataAction, OnlyExportDataAction, run_view_by_celery_task
from common.core.modelset.input_types import get_format_intput_type, get_upload_input_type_suffix
from common.core.modelset.metadata import ChoicesAction, SearchColumnsAction, SearchFieldsAction
from common.core.modelset.upload import UploadFileAction
from common.core.modelset.viewsets import (
    BaseModelSet,
    DetailUpdateModelSet,
    ListDeleteModelSet,
    NoDetailModelSet,
    OnlyListModelSet,
)

__all__ = [
    # base
    "BaseViewSet",
    # crud
    "CreateAction",
    "DetailAction",
    "ListAction",
    "DestroyAction",
    "UpdateAction",
    # batch
    "RankAction",
    "BatchDestroyAction",
    # metadata
    "ChoicesAction",
    "SearchFieldsAction",
    "SearchColumnsAction",
    "get_upload_input_type_suffix",
    "get_format_intput_type",
    # import / export
    "OnlyExportDataAction",
    "ImportExportDataAction",
    "run_view_by_celery_task",
    # upload
    "UploadFileAction",
    # cache
    "CacheDetailResponseMixin",
    "CacheListResponseMixin",
    # composed viewsets
    "DetailUpdateModelSet",
    "OnlyListModelSet",
    "BaseModelSet",
    "ListDeleteModelSet",
    "NoDetailModelSet",
]
