#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：数据结构。"""

from dataclasses import dataclass
from dataclasses import field as dc_field

from system.models import Menu


@dataclass
class RouteInfo:
    view: str
    name: str
    url: str
    sample: str
    actions: dict
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
    model_pks: list
    rank: int = 10000
    source: str = "generator"  # generator / fallback

    @property
    def path(self) -> str:
        return self.url


@dataclass
class BindingFix:
    menu: Menu
    action: str
    mode: str  # add / clear
    current: set = dc_field(default_factory=set)
    expected: set = dc_field(default_factory=set)
