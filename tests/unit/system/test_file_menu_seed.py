# -*- coding: utf-8 -*-
"""文件管理菜单/权限种子守护测试。

新增 REST 接口若不登记权限点、或已登记但未授权给「有该页面的角色」，非超管用户
会被 `IsAuthenticated` 的路径匹配 fail-closed 拒绝（403），且现象与「权限点 path
写错」完全一样。这里把三层关系钉死：

1. 文件接口权限点清单存在、path / method 与端点一致、挂在文件页面菜单下；
2. 权限点 path 正则覆盖真实端点（`django.urls.resolve` 可解析 + 正则命中），
   含 `stats` 的独立权限点语义；
3. 有文件页面授权的内置角色同步授权接口权限点——管理员全量；演示模式只读展示，
   写操作（建改删/上传/物理清除）不出现在其 allow-list。
"""

import json
import os
import re

import pytest
from django.conf import settings as dj_settings
from django.urls import resolve

from system.services.seeding import read_model_rows

LOADJSON_DIR = os.path.join(dj_settings.PROJECT_DIR, "loadjson")
FILE_MENU_PK = "56e7a4c2-97ec-42b1-a8a9-54c3f4e488f3"

# 权限码 → (种子 path 正则, HTTP 方法, 真实端点样例)
FILE_POINTS = {
    "list:SystemUploadFile": ("api/file/file$", "GET", "/api/file/file"),
    "create:SystemUploadFile": ("api/file/file$", "POST", "/api/file/file"),
    "retrieve:SystemUploadFile": (r"api/file/file/(?P<pk>[^/.]+)$", "GET", "/api/file/file/1"),
    "partialUpdate:SystemUploadFile": (r"api/file/file/(?P<pk>[^/.]+)$", "PATCH", "/api/file/file/1"),
    "destroy:SystemUploadFile": (r"api/file/file/(?P<pk>[^/.]+)$", "DELETE", "/api/file/file/1"),
    "batchDestroy:SystemUploadFile": ("api/file/file/batch-destroy$", "POST", "/api/file/file/batch-destroy"),
    "upload:SystemUploadFile": ("api/file/file/upload$", "POST", "/api/file/file/upload"),
    "config:SystemUploadFile": ("api/file/file/config$", "GET", "/api/file/file/config"),
    "download:SystemUploadFile": (r"api/file/file/(?P<pk>[^/.]+)/download$", "GET", "/api/file/file/1/download"),
    "stats:SystemUploadFile": ("api/file/file/stats$", "GET", "/api/file/file/stats"),
    "preview:SystemUploadFile": (r"api/file/file/(?P<pk>[^/.]+)/preview$", "GET", "/api/file/file/1/preview"),
    "accessLogs:SystemUploadFile": (
        r"api/file/file/(?P<pk>[^/.]+)/access-logs$",
        "GET",
        "/api/file/file/1/access-logs",
    ),
    "recycleList:SystemUploadFile": ("api/file/file/recycle$", "GET", "/api/file/file/recycle"),
    "recycleRestore:SystemUploadFile": ("api/file/file/recycle/restore$", "PATCH", "/api/file/file/recycle/restore"),
    "recyclePurge:SystemUploadFile": ("api/file/file/recycle/purge$", "DELETE", "/api/file/file/recycle/purge"),
    "chunkInit:SystemUploadFile": ("api/file/file/chunk/init$", "POST", "/api/file/file/chunk/init"),
    "chunkPart:SystemUploadFile": ("api/file/file/chunk/part$", "POST", "/api/file/file/chunk/part"),
    "chunkComplete:SystemUploadFile": ("api/file/file/chunk/complete$", "POST", "/api/file/file/chunk/complete"),
    "chunkAbort:SystemUploadFile": ("api/file/file/chunk/abort$", "POST", "/api/file/file/chunk/abort"),
}

#: 统计端点使用独立权限点
STATS_PATH = "api/file/file/stats$"

ROLE_ADMIN = "admin"  # 管理员


def _load(name: str):
    with open(os.path.join(LOADJSON_DIR, name), encoding="utf-8") as fp:
        return json.load(fp)


@pytest.fixture(scope="module")
def menus_by_name():
    return {
        item["fields"]["name"]: item for item in read_model_rows("system.menu") if item["fields"].get("menu_type") == 2
    }


def permission_pk(name: str) -> str:
    for item in read_model_rows("system.menu"):
        if item["fields"].get("name") == name and item["fields"].get("menu_type") == 2:
            return item["pk"]
    raise AssertionError(f"权限点 {name} 未登记")


def role_menu_pks(code: str) -> set[str]:
    """种子角色的菜单主键：授权已迁到各层 ``seeds/grants.json``，按名解析回主键。"""

    from system.services.seeding import seed_role_menu_names

    names = set(seed_role_menu_names(code))
    return {item["pk"] for item in read_model_rows("system.menu") if item["fields"].get("name") in names}


def test_file_permission_points_registered(menus_by_name):
    for name, (path, method, __) in FILE_POINTS.items():
        item = menus_by_name.get(name)
        assert item is not None, f"权限点 {name} 未登记进 loadjson/menu.json"
        assert item["fields"]["path"] == path, f"{name} 的 path 与端点不一致（尾斜杠/正则写错即 403）"
        assert item["fields"]["method"] == method, f"{name} 的 method 不一致"
        assert item["fields"]["parent"] == FILE_MENU_PK, f"{name} 必须挂在文件页面菜单下"
        assert item["fields"]["is_active"] is True


def test_file_permission_points_match_real_endpoints(menus_by_name):
    """种子 path 正则必须覆盖真实端点：先确认端点存在，再确认正则命中。"""
    for name, (path, method, url) in FILE_POINTS.items():
        match = resolve(url)
        assert match.func is not None, f"{url} 无法解析（路由未注册）"
        assert re.match(f"/{path}", url), f"{name} 的 path 正则匹配不到真实端点 {url}"
        assert menus_by_name[name]["fields"]["method"] == method


def test_stats_endpoint_has_independent_permission(menus_by_name):
    """统计端点使用独立权限点，不再借 retrieve 代填权限边界。"""
    item = menus_by_name["stats:SystemUploadFile"]
    assert item["fields"]["path"] == STATS_PATH
    assert item["fields"]["method"] == "GET"
    assert item["fields"]["parent"] == FILE_MENU_PK


def test_serialized_actions_bind_model(menus_by_name):
    """有序列化输出的非 CRUD 权限点必须绑定模型，否则角色页配不了白名单 → 输出空对象。

    access-logs 返回 FileAccessLogSerializer、回收站列表返回文件列表：二者无绑定
    时字段权限零字段 fail-closed，非超管拿到的是空对象行（按钮显示但内容全空）。
    """
    assert menus_by_name["accessLogs:SystemUploadFile"]["fields"]["model"] == [
        "bbae5bc8-9426-49d3-991c-e0fcac26a48f"
    ], "access-logs 须绑定 file.fileaccesslog 模型（字段白名单的取值面）"
    assert menus_by_name["recycleList:SystemUploadFile"]["fields"]["model"] == [
        "771d52ec-3633-41c4-89b4-2ca1f0ba4997"
    ], "回收站列表须绑定 file.uploadfile 模型"


def test_admin_role_covers_all_file_points():
    """管理员：文件页面下全部接口权限点均需授权（含回收站与分块上传）。"""
    menus = role_menu_pks(ROLE_ADMIN)
    assert FILE_MENU_PK in menus, "管理员缺少文件页面菜单"
    wanted = {permission_pk(name) for name in FILE_POINTS}
    missing = wanted - menus
    assert not missing, f"管理员缺少文件权限点：{missing}"


def test_demo_allow_list_grants_readonly_file_points():
    """演示模式保留文件中心只读展示；上传/建改删/物理清除仍不得进入 allow-list。"""
    from demo_seed.permissions import DEMO_ALLOWED_PERMISSION_NAMES, DEMO_PAGE_NAMES

    assert "SystemUploadFile" in DEMO_PAGE_NAMES
    read_only = {
        "accessLogs:SystemUploadFile",
        "config:SystemUploadFile",
        "download:SystemUploadFile",
        "list:SystemUploadFile",
        "preview:SystemUploadFile",
        "retrieve:SystemUploadFile",
    }
    assert read_only <= DEMO_ALLOWED_PERMISSION_NAMES

    writes = {
        "create:SystemUploadFile",
        "partialUpdate:SystemUploadFile",
        "destroy:SystemUploadFile",
        "batchDestroy:SystemUploadFile",
        "upload:SystemUploadFile",
        "recyclePurge:SystemUploadFile",
        "chunkInit:SystemUploadFile",
        "chunkPart:SystemUploadFile",
        "chunkComplete:SystemUploadFile",
        "chunkAbort:SystemUploadFile",
    }
    assert not (writes & DEMO_ALLOWED_PERMISSION_NAMES)
