#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""system app 的权限同步声明：平台级路由例外与自管权限面豁免。"""

from common.core.declarations import AppDeclarations

DECLARATIONS = AppDeclarations(
    app="system",
    parent_menu_map={
        # 代码生成器 GUI（generate_crud 引擎的只读端点）
        "api/system/codegen": "SystemCodeGen",
    },
    audit_skip_prefixes=(
        # api-docs/*、api/flower/*：路由以无名 pattern / 代理注册（server/urls.py 直挂），
        # get_all_url_dict 忽略无名路由，故不出现在扫描面内；
        # api/system/global-search：同为无名 pattern 注册（system/urls.py 尾部拼接）。
        "api-docs/",
        "api/flower/",
        "api/system/global-search",
    ),
    permission_exempt_views={
        # PAT 作用域自管配置（PatScopePermission），按令牌范围鉴权而非菜单权限点
        "system.views.user.configs.ConfigsViewSet": "PAT 作用域自管配置（PatScopePermission），按令牌范围鉴权而非菜单权限点",
    },
)
