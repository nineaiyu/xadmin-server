#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""demo app 的权限同步声明：示例模块不投入权限点扫描，权限点由自身种子命令维护。"""

from common.core.declarations import AppDeclarations

# 运行期自建权限点的 pk 段（demo_seed 的 seed_demo_book 命令以 6eed000c 段固定 pk
# 灌入示例目录/菜单/权限点，与内置种子的 5eed 段区分）：绑定与字段白名单由
# demo 自身的种子命令维护，权限点扫描不校正、种子回写不收录。
# 守护：tests/unit/system/test_app_declarations.py 校验本前缀与 seed_demo_book
# 的固定 pk 段一致（改 pk 段必须同步改这里）。
RUNTIME_PERMISSION_PK_PREFIXES = ("6eed000c",)

DECLARATIONS = AppDeclarations(
    app="demo",
    # 不参与扫描的路由前缀（示例 app 按既定决策不投入：权限点清单由 demo_seed 的
    # seed_demo_book 命令维护，不走 loadjson 权限点扫描）
    skip_route_prefixes=("api/demo/",),
    runtime_permission_pk_prefixes=RUNTIME_PERMISSION_PK_PREFIXES,
)
