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

from typing import Any

from django.core.management.base import BaseCommand

from .analysis import FIELD_CLASS_OPTIONS, AnalysisMixin
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
from .registration import RegistrationMixin
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


class Command(AnalysisMixin, RegistrationMixin, MergeMixin, RenderMixin, BaseCommand):
    help = "Generate CRUD scaffold (serializer/views/urls/config + client page + menu seed) for an existing model"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("model", help="目标模型标签，如 demo.Book")
        parser.add_argument("--component", default="", help="前端组件名（默认 App+Model 驼峰，如 DemoBook）")
        parser.add_argument("--url-prefix", default="", help="API 前缀（默认 api/<app>/<model>）")
        parser.add_argument("--frontend-dir", default="", help="前端目录（相对 src/views，默认 <app>/<model>）")
        parser.add_argument(
            "--frontend-root",
            default="",
            help="前端仓库根（优先级：本旗标 > XADMIN_CLIENT_DIR 环境变量 > 同级 xadmin-client）",
        )
        parser.add_argument("--parent", default="", help="菜单种子的上级菜单 pk（默认顶级）")
        parser.add_argument("--menu-title", default="", help="菜单种子的菜单标题（默认模型 verbose_name）")
        parser.add_argument(
            "--ordering",
            default="",
            help="列表视图默认排序（单个模型字段名，可带 - 前缀；默认 created_time/pk 倒序推导）",
        )
        parser.add_argument(
            "--skip-ai",
            action="store_true",
            help="不生成 AI 动作声明骨架（默认生成，纯内部管理表等场景可跳过）",
        )
        parser.add_argument("--menu-icon", default="", help="菜单种子的图标（默认 ep:document，如 ep:grid）")
        parser.add_argument(
            "--dict-field",
            action="append",
            default=[],
            metavar="字段=字典code",
            help="字段绑定数据字典（生成 DictChoiceField 声明，可多次）：如 --dict-field status=task_status",
        )
        parser.add_argument(
            "--field-class",
            action="append",
            default=[],
            metavar="字段=字段类",
            help="字段声明为专用字段类（GUI 表单同源能力，可多次）：如 --field-class phone=PhoneField，"
            f"可选 {' / '.join(FIELD_CLASS_OPTIONS)}",
        )
        parser.add_argument("--with-import-export", action="store_true", help="ViewSet 追加导入导出 Mixin 与权限码")
        parser.add_argument("--with-tags", action="store_true", help="AI 声明文件附带标签接入声明（白名单）")
        parser.add_argument("--with-tests", action="store_true", help="生成 pytest 测试骨架（tests/unit/<app>/）")
        parser.add_argument("--with-module", action="store_true", help="同时生成 {app}/modules.py 模块声明（可裁剪）")
        parser.add_argument(
            "--register-app",
            action="store_true",
            help="生成后把 app 写入 config.yml 的 XADMIN_APPS（幂等，打印 diff；改后需重启进程）",
        )
        parser.add_argument("--module-id", default="", help="模块 id（默认 app label，仅 --with-module 时使用）")
        parser.add_argument(
            "--module-level",
            choices=("core", "standard", "optional"),
            default="optional",
            help="模块等级（默认 optional，随 full 预设默认开启；仅 --with-module 时使用）",
        )
        parser.add_argument("--output", default="", help="后端输出根（默认项目根）")
        parser.add_argument("--skip-frontend", action="store_true", help="只生成后端与菜单种子")
        parser.add_argument("--skip-menu-seed", action="store_true", help="不生成菜单种子 JSON")
        parser.add_argument("--dry-run", action="store_true", help="只打印产物，不落盘")
        parser.add_argument("--force", action="store_true", help="覆盖已存在的生成文件（共享文件仍走生成块合并）")
        parser.add_argument(
            "--bootstrap",
            action="store_true",
            help="生成后一条龙幂等入库：sync_model_field（字段权限树）→ 回填种子 model 关联 → loaddata 菜单/权限点",
        )
        parser.add_argument(
            "--grant-to",
            default="",
            help="仅 --bootstrap 生效：把本页菜单与权限点授予指定角色（逗号分隔角色 code，如 admin,ops）；授权属业务决策，不传则跳过",
        )
        parser.add_argument(
            "--verify",
            action="store_true",
            help="生成后运行权限缺口与 OpenAPI 契约漂移检查（只报告，不阻断生成）",
        )

    # ------------------------------------------------------------------ 入口

    def handle(self, *args: Any, **options: Any) -> None:
        model = self._resolve_model(options["model"])
        ctx = self._build_context(model, options)
        self._apply_dict_fields(ctx, options.get("dict_field") or [])
        self._apply_field_classes(ctx, options.get("field_class") or [])
        artifacts = self._collect_artifacts(ctx, options)
        self._emit(artifacts, options)
        ctx["app_registered"] = False
        if options.get("register_app"):
            ctx["app_registered"] = self._register_app(ctx, options)
        if options.get("bootstrap"):
            self._bootstrap(ctx, options)
        if options.get("verify") and not options.get("dry_run"):
            self._verify_generated()
        self._print_next_steps(ctx, options)

    def _verify_generated(self) -> None:
        """生成后自检：权限缺口 + OpenAPI 契约漂移（问题只报告，不阻断生成）。"""
        from io import StringIO

        from django.core.management import call_command

        self.stdout.write("\n生成后自检：")
        buffer = StringIO()
        try:
            call_command("check", stdout=buffer, stderr=buffer)
            self.stdout.write("  - Django system check：通过")
        except Exception as exc:  # noqa: BLE001 只报告，生成物已落盘
            self.stdout.write(f"  - Django system check：失败（{exc}）")

        buffer = StringIO()
        try:
            call_command("sync_menu_permissions", dry_run=True, stdout=buffer)
            summary = [line for line in buffer.getvalue().splitlines() if "缺口" in line or "路由" in line]
            self.stdout.write("  - 权限点：")
            for line in summary[-3:]:
                self.stdout.write(f"      {line.strip()}")
        except Exception as exc:  # noqa: BLE001 应用未注册 / URLconf 导入失败等
            self.stdout.write(f"  - 权限点检查跳过（{exc}）")

        buffer = StringIO()
        try:
            call_command("dump_openapi", check=True, stdout=buffer, stderr=buffer)
            self.stdout.write("  - OpenAPI 契约：与 docs/schema/openapi.json 一致")
        except Exception:  # noqa: BLE001 新增端点后 schema 尚未重导出属预期
            self.stdout.write(
                "  - OpenAPI 契约：已漂移（运行 python manage.py dump_openapi 后执行 pnpm sync:contract）"
            )
