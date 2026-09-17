#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：已存在的模型 → 后端四件套 + 前端页面 + 菜单种子 JSON。

定位：**一次性代码生成**（非运行期脚手架）——模板与仓库既有范式同源
（docs/architecture/framework-cookbook.md、xadmin-docs example/new-app-*.md），
产物是普通仓库代码，生成后由开发者继续编辑并按常规门禁提交。

约定：
- 输入是已存在的 Django 模型（模型是唯一真源，本命令不改模型、不建迁移）；
- 已存在的文件默认跳过（`--force` 覆盖）；共享文件（views.py / serializers.py）走
  ``# --- xadmin:generated:<key>:start ---`` 生成块幂等合并，块内 import 按目标文件
  已有导入去重（避免 F811 重定义）；urls.py 走注册行插入（幂等）；
- 菜单种子 pk 用固定 uuid5 命名空间派生：同输入重复生成同一批 pk，重复 loaddata = 覆盖同一批行。

用法与参数见 `python manage.py generate_crud --help`。

本包为命令实现（constants / analysis / merging / renderers 四个 Mixin）：
Django 命令发现只识别模块、跳过包目录，命令入口是同目录的
``generate_crud.py``（仅做再导出），因此本包不参与命令注册。
"""

from django.core.management.base import BaseCommand

from .analysis import AnalysisMixin
from .constants import (
    AUDIT_FIELDS,
    BLOCK_END,
    BLOCK_START,
    FILE_RELATED_MODEL,
    FIRST_PARTY_TOP_LEVEL,
    IMPORT_EXPORT_PERMISSIONS,
    PERMISSION_ACTIONS,
    SEARCH_EXCLUDE_TYPES,
    SEED_NAMESPACE,
)
from .merging import MergeMixin
from .renderers import RenderMixin

__all__ = [
    "AUDIT_FIELDS",
    "BLOCK_END",
    "BLOCK_START",
    "Command",
    "FILE_RELATED_MODEL",
    "FIRST_PARTY_TOP_LEVEL",
    "IMPORT_EXPORT_PERMISSIONS",
    "PERMISSION_ACTIONS",
    "SEARCH_EXCLUDE_TYPES",
    "SEED_NAMESPACE",
]


class Command(AnalysisMixin, MergeMixin, RenderMixin, BaseCommand):
    help = "Generate CRUD scaffold (serializer/views/urls/config + client page + menu seed) for an existing model"

    def add_arguments(self, parser):
        parser.add_argument("model", help="目标模型标签，如 demo.Book")
        parser.add_argument("--component", default="", help="前端组件名（默认 App+Model 驼峰，如 DemoBook）")
        parser.add_argument("--url-prefix", default="", help="API 前缀（默认 api/<app>/<model>）")
        parser.add_argument("--frontend-dir", default="", help="前端目录（相对 src/views，默认 <app>/<model>）")
        parser.add_argument("--frontend-root", default="", help="前端仓库根（默认同级 xadmin-client，存在时使用）")
        parser.add_argument("--parent", default="", help="菜单种子的上级菜单 pk（默认顶级）")
        parser.add_argument("--with-import-export", action="store_true", help="ViewSet 追加导入导出 Mixin 与权限码")
        parser.add_argument("--output", default="", help="后端输出根（默认项目根）")
        parser.add_argument("--skip-frontend", action="store_true", help="只生成后端与菜单种子")
        parser.add_argument("--skip-menu-seed", action="store_true", help="不生成菜单种子 JSON")
        parser.add_argument("--dry-run", action="store_true", help="只打印产物，不落盘")
        parser.add_argument("--force", action="store_true", help="覆盖已存在的生成文件（共享文件仍走生成块合并）")

    # ------------------------------------------------------------------ 入口

    def handle(self, *args, **options):
        model = self._resolve_model(options["model"])
        ctx = self._build_context(model, options)
        artifacts = self._collect_artifacts(ctx, options)
        self._emit(artifacts, options)
