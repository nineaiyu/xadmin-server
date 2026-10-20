# -*- coding: utf-8 -*-
"""内置演示角色的「只读与取用」权限点授权覆盖守护。

背景：新增 `@action` 只登记权限点、却不给「已拥有该页面的角色」授权时，页面会出现
两类故障——前端有 `hasAuth` 守卫的按钮静默消失（功能残缺），无守卫的入口点击 403
（`IsAuthenticated` 按路径匹配 fail-closed）。2026-10 已连续踩中两次（在线用户字段
白名单、文件中心统计 / 访问记录 / 下载）。

规则（以 loadjson 种子为准，与库内状态无关）：
1. 角色拥有某页面（`menu_type=1` 的菜单）时，其下**只读与取用类**权限点必须一并授权；
2. 只读与取用类按动作名前缀判定（list / retrieve / download / preview / accessLogs /
   stats / recycleList / recycleRestore / read / unread / backends / select）；
3. 破坏与写入类（destroy / batchDestroy / recyclePurge / forceLogout / impersonate /
   create / partialUpdate / upload / export* / import* / 审批动作等）不参与本守护，
   由各角色自己的口径决定（演示角色刻意不给写操作）。

豁免（`EXEMPT_POINTS`，附理由）：复用同一端点、显隐由其他权限点决定的页面级权限点。
"""

import json
import os
import re
from typing import Any

from django.conf import settings as dj_settings

from demo_seed.permissions import DEMO_ALLOWED_PERMISSION_NAMES
from system.services.seeding import read_model_rows

LOADJSON_DIR = os.path.join(dj_settings.PROJECT_DIR, "loadjson")

#: 参与守护的角色（内置角色，按种子 name 匹配）。test 为开发自测角色，
#: 2026-10 审计发现其「页面 ⇒ 只读子权限点」缺口（角色页预览/回收站、模型字段
#: 详情、消息订阅详情）后纳入守护，与另两角色同规则。
GUARDED_ROLES = ("管理员", "演示模式", "test")

#: 只读与取用类动作名前缀
READONLY_PREFIXES = (
    "list:",
    "retrieve:",
    "download:",
    "preview:",
    "accessLogs:",
    "stats:",
    "recycleList:",
    "recycleRestore:",
    "read:",
    "unread:",
    "backends:",
    "select:",
)

#: 豁免清单：复用端点 / 显隐由其他权限点决定的页面级权限点
EXEMPT_POINTS = {
    # 变更历史：复用操作日志端点（api/audit/logs/operation$），入口显隐按 list:SystemOperationLog
    "changeHistory:SystemUser",
    "changeHistory:SystemDept",
    "changeHistory:DemoBook",
    # 水印配置读取：与 retrieve:SettingBasic 同端点前缀（api/settings/basic$）且页面入口共用一个
    "retrieve:SettingWatermark",
}


def _load(name: str) -> Any:
    with open(os.path.join(LOADJSON_DIR, name), encoding="utf-8") as fp:
        return json.load(fp)


def _parent_page(pk: str, by_pk: dict[str, Any]) -> str | None:
    """沿父链找到最近的页面菜单（menu_type=1）主键。"""
    node = by_pk.get(pk)
    for _ in range(4):
        if not node:
            return None
        parent_id = node.get("parent")
        if not parent_id:
            return None
        parent = by_pk.get(parent_id)
        if not parent:
            return None
        if parent.get("menu_type") == 1:
            return parent_id
        node = parent
    return None


def _whitelisted(point: dict[str, Any]) -> bool:
    """与运行时同口径：白名单 URL 不要求菜单权限（个人自服务端点等）。"""
    path = point.get("path") or ""
    method = (point.get("method") or "").upper()
    sample = "/" + re.sub(r"\(\?P<[^>]+>\[\^/\.\]\+\)", "1", path.rstrip("$"))
    for pattern, methods in dj_settings.PERMISSION_WHITE_URL.items():
        if re.match(pattern, sample) and (method in methods or "*" in methods):
            return True
    return False


def role_menu_pks(code: str) -> set[str]:
    """种子角色的菜单主键：授权已迁到各层 ``seeds/grants.json``，按名解析回主键。"""

    from system.services.seeding import seed_role_menu_names

    names = set(seed_role_menu_names(code))
    return {row["pk"] for row in read_model_rows("system.menu") if row["fields"].get("name") in names}


def test_demo_roles_cover_readonly_points_of_owned_pages() -> None:
    menus = [row["fields"] | {"pk": row["pk"]} for row in read_model_rows("system.menu")]
    by_pk = {row["pk"]: row for row in menus}
    points = [row for row in menus if row.get("menu_type") == 2]
    roles = {row["fields"].get("name"): role_menu_pks(row["fields"].get("code")) for row in _load("userrole.json")}

    missing: list[str] = []
    for role_name in GUARDED_ROLES:
        owned = roles.get(role_name)
        assert owned is not None, f"种子角色缺失：{role_name}"
        for point in points:
            name = point.get("name") or ""
            if not name.startswith(READONLY_PREFIXES) or name in EXEMPT_POINTS:
                continue
            # 演示模式按 allow-list 收敛：清单外的只读点（回收站/导出等）刻意不授予
            if role_name == "演示模式" and name not in DEMO_ALLOWED_PERMISSION_NAMES:
                continue
            if point["pk"] in owned or _whitelisted(point):
                continue
            page = _parent_page(point["pk"], by_pk)
            if page and page in owned:
                missing.append(f"{role_name} × {name}（页面 {by_pk[page].get('title') or by_pk[page].get('name')}）")

    assert missing == [], (
        "内置演示角色拥有页面，但缺少其下的只读与取用类权限点（按钮会静默消失或入口点击 403）；"
        "新增只读接口后请同步授权（userrole.json），确属复用端点/其它显隐口径的请登记 EXEMPT_POINTS：\n"
        + "\n".join(missing)
    )
