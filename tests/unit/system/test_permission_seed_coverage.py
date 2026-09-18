# -*- coding: utf-8 -*-
"""菜单种子权限点覆盖守护：所有需要鉴权的 API 端点都必须被 loadjson/menu.json 覆盖。

把 ``sync_menu_permissions --dry-run`` 的全量扫描口径固化为 CI 门禁（此前只有人工体检）：

- **路由面**：与命令同源的 ``build_route_index``（仅 api/ 前缀，与运行时
  ``requires_permission`` 同口径，跳过 PUT / HEAD 与白名单；HEAD 由 DRF 在请求链路中
  自动映射到 GET handler，无需独立权限点——内核 ``scan_gaps`` 已统一口径）；
- **权限点面**：直接读 ``loadjson/menu.json``（menu_type=2 记录的 path/method），
  不依赖数据库 —— 种子是「新库开箱权限」的唯一来源；
- **断言**：``scan_gaps`` 为空。新增 ViewSet 端点忘记登记权限点会在 CI 红灯，
  而不是上线后「全员 403」；补齐方式：
  ``python manage.py sync_menu_permissions --update-seed``。
"""

import json
import os
from types import SimpleNamespace

from django.conf import settings

from system.utils.permission_sync import build_route_index, scan_gaps

MENU_SEED_FILE = "menu.json"


def _seed_permissions():
    """loadjson/menu.json 的权限点（menu_type=2），以 (path, method) 载体返回。"""
    path = os.path.join(settings.PROJECT_DIR, "loadjson", MENU_SEED_FILE)
    with open(path, encoding="utf-8") as fp:
        rows = json.load(fp)
    return [
        SimpleNamespace(
            path=row["fields"]["path"], method=row["fields"].get("method") or "", name=row["fields"]["name"]
        )
        for row in rows
        if row["fields"].get("menu_type") == 2
    ]


class TestRouteScanSanity:
    def test_route_index_is_not_empty(self):
        """扫描面非空（防 URLconf 未加载导致空集假绿）。"""
        routes = build_route_index()
        assert len(routes) > 100, f"路由扫描结果异常（{len(routes)} 条），检查 URLconf 加载"

    def test_seed_permissions_loaded(self):
        perms = _seed_permissions()
        assert len(perms) > 100, f"菜单种子权限点异常（{len(perms)} 个）"


class TestSeedCoverage:
    def test_every_required_route_covered_by_menu_seed(self):
        """需要鉴权的端点必须落在菜单种子的 (path, method) 覆盖面内。"""
        routes = build_route_index()
        gaps = scan_gaps(routes, _seed_permissions())
        detail = "\n".join(f"  {method:6s} {route.url} -> {action}" for route, method, action in gaps)
        assert gaps == [], (
            f"以下 {len(gaps)} 个端点缺少菜单种子权限点（跑 sync_menu_permissions --update-seed 补齐）：\n{detail}"
        )
