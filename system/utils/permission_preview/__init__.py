#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : permission_preview
# author : ly_13
# date : 9/7/2026
"""权限可视化（规划外功能）：三层权限只读预览 + 数据权限实时试算。

设计原则：
- 预览 = 真实计算逻辑的只读复用，全部**直查 DB**，不走 MagicCacheData 权限缓存
  （API 权限 24h / 字段权限 10s 缓存会使预览失真；数据权限 get_filter_queryset
  每次现查规则，天然新鲜），保证"所见即当前配置"。
- 以任意 user 为主语取数，不依赖当前请求（复用 get_user_menu_queryset；
  超管自行走 Menu.objects.filter(is_active=True) 旁路，与 routes 视图口径一致）。
- 试算向目标 user 注入 `menu` 属性模拟菜单上下文，与 IsAuthenticated 写入
  request.user.menu 完全同构（common/core/permission.py L123）。
- 文案为面向管理员的中文直述（同登录限流等既有中文文案惯例），不入 .po。

本包按职责拆分（constants / labels / decode / queries / trial_data / trial_field /
previews），对外 API 由本文件统一再导出，导入路径保持
``system.utils.permission_preview`` 不变。
"""

from .constants import DEPT_PREVIEW_NOTES, PREVIEW_USER_SAMPLE_LIMIT, PREVIEW_VALUE_NAME_LIMIT, TRIAL_SAMPLE_LIMIT
from .decode import decode_data_permission, decode_rule
from .previews import get_dept_preview, get_role_preview, get_user_preview
from .queries import (
    get_trial_candidates,
    get_user_api_permissions,
    get_user_data_permissions,
    get_user_field_matrix,
    get_user_menu_queryset_for_preview,
)
from .trial_data import run_data_trial
from .trial_field import run_field_trial

__all__ = [
    "DEPT_PREVIEW_NOTES",
    "PREVIEW_USER_SAMPLE_LIMIT",
    "PREVIEW_VALUE_NAME_LIMIT",
    "TRIAL_SAMPLE_LIMIT",
    "decode_data_permission",
    "decode_rule",
    "get_dept_preview",
    "get_role_preview",
    "get_trial_candidates",
    "get_user_api_permissions",
    "get_user_data_permissions",
    "get_user_field_matrix",
    "get_user_menu_queryset_for_preview",
    "get_user_preview",
    "run_data_trial",
    "run_field_trial",
]
