# -*- coding: utf-8 -*-
"""聊天室菜单/权限种子守护测试（ADR-034）。

新增 REST 接口若不登记权限点，非超管用户会被 `IsAuthenticated._resolve_menu_pk`
fail-closed 拒绝（403）；而权限点 `path` 写错（如多写尾斜杠）**同样**是 403，
且现象一模一样、极难排查。这里把「种子 → 真实路由」的对应关系钉死：

1. 6 个权限点存在且方法/路径与 §决策 4 一致；
2. 每个权限点路径的正则确实覆盖对应的真实端点（`django.urls.resolve` 可解析 + 正则命中）；
3. 权限点挂在聊天室页面菜单下，且 4 个内置角色均已授权（否则普通用户进不去聊天室）。
"""

import json
import os
import re

import pytest
from django.conf import settings as dj_settings
from django.urls import resolve

LOADJSON_DIR = os.path.join(dj_settings.PROJECT_DIR, "loadjson")
CHAT_MENU_PK = "3949728a-8f7c-45a8-b84f-122b82e9c45e"

# 权限码 → (种子 path 正则, HTTP 方法, 真实端点样例)
CHAT_POINTS = {
    "list:ChatRoom": ("api/chat/room$", "GET", "/api/chat/room"),
    "create:ChatRoom": ("api/chat/room/open-private$", "POST", "/api/chat/room/open-private"),
    "list:ChatMessage": ("api/chat/message$", "GET", "/api/chat/message"),
    "recall:ChatMessage": (r"api/chat/message/(?P<pk>[^/.]+)/recall$", "POST", "/api/chat/message/1/recall"),
    "list:ChatContact": ("api/chat/contacts$", "GET", "/api/chat/contacts"),
    "ask:ChatRoom": ("api/chat/ai/message$", "POST", "/api/chat/ai/message"),
}
# 已授予聊天室权限的内置角色（有聊天室页面授权的角色必须同步授权接口权限点）
ROLES_WITH_CHAT = [
    "069ea080-66b5-4ce3-9047-e3044ee98f30",  # 管理员
    "852a242e-e976-4475-8a25-7116087f4c78",  # 默认权限
    "9be1b2f6-405c-4978-8bca-093f566b8d3c",  # 演示模式
    "2d00af57-daa6-4634-952e-30074f09ed68",  # test
]


def _load(name: str):
    with open(os.path.join(LOADJSON_DIR, name), encoding="utf-8") as fp:
        return json.load(fp)


@pytest.fixture(scope="module")
def menus_by_name():
    return {item["fields"]["name"]: item for item in _load("menu.json") if item["fields"].get("menu_type") == 2}


def test_chat_permission_points_registered(menus_by_name):
    for name, (path, method, __) in CHAT_POINTS.items():
        item = menus_by_name.get(name)
        assert item is not None, f"权限点 {name} 未登记进 loadjson/menu.json"
        assert item["fields"]["path"] == path, f"{name} 的 path 与端点不一致（尾斜杠/正则写错即 403）"
        assert item["fields"]["method"] == method, f"{name} 的 method 不一致"
        assert item["fields"]["parent"] == CHAT_MENU_PK, f"{name} 必须挂在聊天室页面菜单下"
        assert item["fields"]["is_active"] is True


def test_chat_permission_points_match_real_endpoints(menus_by_name):
    """种子 path 正则必须覆盖真实端点：先确认端点存在，再确认正则命中。"""
    for name, (path, method, url) in CHAT_POINTS.items():
        match = resolve(url)
        assert match.func is not None, f"{url} 无法解析（路由未注册）"
        assert re.match(f"/{path}", url), f"{name} 的 path 正则匹配不到真实端点 {url}"
        assert menus_by_name[name]["fields"]["method"] == method


def permission_pk(name: str) -> str:
    """按权限码取菜单主键（供角色授权断言使用）。"""
    for item in _load("menu.json"):
        if item["fields"].get("name") == name and item["fields"].get("menu_type") == 2:
            return item["pk"]
    raise AssertionError(f"权限点 {name} 未登记")


def test_builtin_roles_granted_chat_permissions():
    """有聊天室页面的内置角色必须同时获得 6 个接口权限点。"""
    wanted = {permission_pk(name) for name in CHAT_POINTS}
    roles = {item["pk"]: item for item in _load("userrole.json")}
    for role_pk in ROLES_WITH_CHAT:
        role = roles.get(role_pk)
        assert role is not None, f"角色 {role_pk} 不存在"
        menus = set(role["fields"].get("menu", []))
        assert CHAT_MENU_PK in menus, "角色缺少聊天室页面菜单"
        missing = wanted - menus
        assert not missing, f"角色 {role['fields']['name']} 缺少聊天室权限点：{missing}"
