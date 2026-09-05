#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""预组合的通用 ViewSet 集合。

对外提供 BaseModelSet 等标准组合，业务视图按需选用。
拆分自 modelset.py（T2.1），组合顺序与拆分前保持一致，MRO 不变。
"""

from rest_framework.viewsets import GenericViewSet

from common.core.modelset.base import BaseViewSet
from common.core.modelset.batch import BatchDestroyAction
from common.core.modelset.crud import CreateAction, DestroyAction, DetailAction, ListAction, UpdateAction
from common.core.modelset.metadata import SearchColumnsAction, SearchFieldsAction


class DetailUpdateModelSet(BaseViewSet, UpdateAction, DetailAction, GenericViewSet):
    pass


class OnlyListModelSet(BaseViewSet, ListAction, SearchFieldsAction, SearchColumnsAction, GenericViewSet):
    pass


# 全部 ViewSet 包含增删改查
class BaseModelSet(
    BaseViewSet,
    CreateAction,
    DestroyAction,
    UpdateAction,
    ListAction,
    DetailAction,
    SearchFieldsAction,
    SearchColumnsAction,
    BatchDestroyAction,
    GenericViewSet,
):
    pass


# 只允许读和删除，不允许创建和修改
class ListDeleteModelSet(
    BaseViewSet,
    DestroyAction,
    ListAction,
    DetailAction,
    SearchFieldsAction,
    SearchColumnsAction,
    BatchDestroyAction,
    GenericViewSet,
):
    pass


class NoDetailModelSet(BaseViewSet, UpdateAction, DetailAction, SearchColumnsAction, GenericViewSet):
    pass
