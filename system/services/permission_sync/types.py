#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：数据结构。"""

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from system.models import Menu

#: 自动生成权限点菜单的起始 rank（与手工菜单的 rank 取值区间错开，留出插入余量）
PERMISSION_MENU_RANK_BASE = 10000


@dataclass
class RouteInfo:
    view: str
    name: str
    url: str
    sample: str
    actions: dict[str, Any]
    view_cls: object
    requires_permission: bool


@dataclass
class PlanItem:
    view: str
    url: str
    method: str
    action: str
    code: str
    description: str
    parent_id: object
    parent_name: str
    model_pks: list[Any]
    rank: int = PERMISSION_MENU_RANK_BASE
    source: str = "generator"  # generator / fallback

    @property
    def path(self) -> str:
        return self.url


@dataclass
class BindingFix:
    menu: Menu
    action: str
    mode: str  # add / clear
    current: set[Any] = dc_field(default_factory=set[Any])
    expected: set[Any] = dc_field(default_factory=set[Any])
