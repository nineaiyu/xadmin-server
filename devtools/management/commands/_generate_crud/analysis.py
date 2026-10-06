#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：模型解析、字段规划与产物收集。"""

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.conf import settings
from django.core.management.base import CommandError

from common.contracts import Menu, UserRole, sync_model_field

from .constants import IMPORT_EXPORT_PERMISSIONS, PERMISSION_ACTIONS
from .field_plan import FieldPlanMixin


class AnalysisMixin(FieldPlanMixin):
    """模型 → 生成上下文与产物清单（不落盘）。"""

    if TYPE_CHECKING:  # 宿主 Command（BaseCommand + 兄弟 mixin）提供的接口（mixin 模式）
        stdout: Any
        style: Any

        def _existing_text(self, *args, **kwargs) -> str: ...

        def _strip_block(self, *args, **kwargs) -> str: ...

        def _model_label_pk(self, *args, **kwargs) -> str: ...

        def _render_serializer_module(self, *args, **kwargs) -> Any: ...

        def _render_views_module(self, *args, **kwargs) -> Any: ...

        def _render_urls_module(self, *args, **kwargs) -> Any: ...

        def _render_config(self, *args, **kwargs) -> Any: ...

        def _render_ai_declarations(self, *args, **kwargs) -> Any: ...

        def _render_test_skeleton(self, *args, **kwargs) -> Any: ...

        def _render_menu_seed(self, *args, **kwargs) -> Any: ...

        def _render_client_api(self, *args, **kwargs) -> Any: ...

        def _render_client_hook(self, *args, **kwargs) -> Any: ...

        def _render_client_page(self, *args, **kwargs) -> Any: ...

    # ------------------------------------------------------------- 上下文构建

    def _resolve_model(self, label):
        if "." not in label:
            raise CommandError("模型标签需形如 <app_label>.<ModelName>，如 demo.Book")
        app_label, model_name = label.split(".", 1)
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError as exc:
            raise CommandError(f"未找到模型 {label}（模型需已注册在 INSTALLED_APPS）") from exc
        if model._meta.abstract:
            raise CommandError(f"{label} 是抽象模型，不能生成 CRUD")
        return model

    def _build_context(self, model, options):
        model_snake = self._snake(model.__name__)
        app_label = model._meta.app_label
        component = options["component"] or f"{app_label.title()}{model.__name__}"
        router_path = model_snake.replace("_", "-")
        ordering = self._parse_ordering(options)
        ctx = {
            "model": model,
            "app_label": app_label,
            "model_name": model.__name__,
            "model_snake": model_snake,
            "verbose_name": str(model._meta.verbose_name),
            "menu_title": str(options.get("menu_title") or "").strip() or str(model._meta.verbose_name),
            "component": component,
            "router_path": router_path,
            "url_prefix": options["url_prefix"] or f"api/{app_label}/{router_path}",
            "frontend_dir": options["frontend_dir"] or f"{app_label}/{model_snake}",
            "locale_name": component[:1].lower() + component[1:],
            "basename": router_path,
            "with_import_export": options["with_import_export"],
            "menu_icon": str(options.get("menu_icon") or "").strip(),
            "default_ordering": ordering or self._default_ordering(model),
        }
        ctx.update(self._field_plan(model))
        return ctx

    @staticmethod
    def _parse_ordering(options) -> str:
        """显式排序覆盖（--ordering）：单个字段名（可带 - 前缀），空串走引擎推导。

        生成物里 ordering 直接落进源码，先做标识符校验防注入；模型已声明
        Meta.ordering 时显式覆盖仍然生效（开发者明确的意图优先）。
        """
        ordering = str(options.get("ordering") or "").strip()
        if not ordering:
            return ""
        if not re.fullmatch(r"-?[A-Za-z_][A-Za-z0-9_]*", ordering):
            raise CommandError(f"--ordering 需为单个模型字段名（可带 - 前缀），收到：{ordering!r}")
        return ordering

    def _apply_dict_fields(self, ctx, pairs):
        """字典绑定：``字段名=字典 code`` 对收敛为 ctx["dict_fields"]（渲染 DictChoiceField 声明）。

        字段名越界即报错（CLI CommandError / GUI CodegenError 文案一致）；字典 code 的
        存在性由调用方校验（CLI 无库环境降级为警告，GUI 侧硬校验）。
        """
        dict_fields = {}
        for pair in pairs:
            name, _, code = str(pair).partition("=")
            name, code = name.strip(), code.strip()
            if not name or not code:
                raise CommandError(f"--dict-field 需形如 <字段名>=<字典code>，收到：{pair!r}")
            known = {item.name for item in ctx["model"]._meta.fields} | {
                item.name for item in ctx["model"]._meta.many_to_many
            }
            if name not in known:
                raise CommandError(f"未知字段：{name}（模型 {ctx['model']._meta.label} 无此字段）")
            dict_fields[name] = code
        if dict_fields:
            ctx["dict_fields"] = dict_fields
        return dict_fields

    # --------------------------------------------------------------- 产物收集

    def _collect_artifacts(self, ctx, options):
        backend_root = Path(options["output"] or settings.PROJECT_DIR)
        app_dir = backend_root / ctx["app_label"]
        artifacts = []

        serializer_package = (app_dir / "serializers").is_dir()
        serializer_path = (
            app_dir / "serializers" / f"{ctx['model_snake']}.py" if serializer_package else app_dir / "serializers.py"
        )
        ctx["serializer_import"] = (
            f"{ctx['app_label']}.serializers.{ctx['model_snake']}"
            if serializer_package
            else f"{ctx['app_label']}.serializers"
        )
        serializer_key = f"serializer-{ctx['model_snake']}"
        artifacts.append(
            {
                "label": "序列化器",
                "path": serializer_path,
                "content": self._render_serializer_module(
                    ctx,
                    existing=""
                    if serializer_package
                    else self._strip_block(self._existing_text(serializer_path), serializer_key),
                    standalone=serializer_package,
                ),
                "mode": "create" if serializer_package else "block",
                "key": serializer_key,
            }
        )

        views_package = (app_dir / "views").is_dir()
        views_path = app_dir / "views" / f"{ctx['model_snake']}.py" if views_package else app_dir / "views.py"
        views_key = f"views-{ctx['model_snake']}"
        ctx["view_module"] = (
            f"{ctx['app_label']}.views.{ctx['model_snake']}" if views_package else f"{ctx['app_label']}.views"
        )
        artifacts.append(
            {
                "label": "视图",
                "path": views_path,
                "content": self._render_views_module(
                    ctx,
                    existing="" if views_package else self._strip_block(self._existing_text(views_path), views_key),
                    standalone=views_package,
                ),
                "mode": "create" if views_package else "block",
                "key": views_key,
            }
        )

        artifacts.append(
            {
                "label": "路由",
                "path": app_dir / "urls.py",
                "content": self._render_urls_module(ctx),
                "mode": "urls",
                "key": f"urls-{ctx['model_snake']}",
                "ctx": ctx,
            }
        )
        artifacts.append(
            {
                "label": "应用配置",
                "path": app_dir / "config.py",
                "content": self._render_config(ctx),
                "mode": "create",
                "key": f"config-{ctx['model_snake']}",
            }
        )

        # 「生成即接入」：AI 动作声明骨架（只读动作直接可用）+ 可选测试骨架；
        # --skip-ai 时不产出（纯内部管理表等无需 AI 接入的场景）
        if not options.get("skip_ai"):
            artifacts.append(
                {
                    "label": "AI 动作声明",
                    "path": app_dir / "ai_declarations.py",
                    "content": self._render_ai_declarations(ctx, options),
                    "mode": "create",
                    "key": f"ai-declarations-{ctx['model_snake']}",
                }
            )
        if options.get("with_tests"):
            artifacts.append(
                {
                    "label": "测试骨架",
                    "path": backend_root / "tests" / "unit" / ctx["app_label"] / f"test_{ctx['model_snake']}_api.py",
                    "content": self._render_test_skeleton(ctx),
                    "mode": "create",
                    "key": f"tests-{ctx['model_snake']}",
                }
            )

        if not options["skip_frontend"]:
            artifacts.extend(self._frontend_artifacts(ctx, options))

        if options["with_module"]:
            artifacts.append(self._module_artifact(ctx, options))

        if not options["skip_menu_seed"]:
            # 菜单的 model 关联（字段权限数据源）：渲染种子前解析一次，后续步骤提示复用
            ctx["model_label_pk"] = self._model_label_pk(ctx["model"])
            artifacts.append(
                {
                    "label": "菜单种子",
                    "path": backend_root / "loadjson" / f"seed_{ctx['app_label']}_{ctx['model_snake']}.json",
                    "content": self._render_menu_seed(ctx, options["parent"]),
                    "mode": "create",
                    "key": f"seed-{ctx['model_snake']}",
                }
            )
        return artifacts

    def _module_artifact(self, ctx, options):
        """可选产物：{app}/modules.py 模块声明（可裁剪模块的脚手架）。

        模块 id 已存在时不中断生成，降级为提示（换 --module-id 或去掉 --with-module）；
        模板与 `generate_module` 同源（common/core/modules/scaffold.py）。
        """
        from common.core.modules import derive_route_prefixes, module_id_conflict, render_modules_source

        module_id = options["module_id"] or ctx["app_label"]
        key = f"modules-{ctx['app_label']}"
        if module_id_conflict(module_id):
            return {
                "label": "模块声明",
                "path": None,
                "content": "",
                "mode": "notice",
                "key": key,
                "notice": f"模块 id 已存在：{module_id}（换 --module-id，或不加 --with-module）",
            }
        app_config = apps.get_app_config(ctx["app_label"])
        # 菜单根 name 取生成的页面菜单名（component）；跳过菜单种子时无从声明，留空
        menus = () if options["skip_menu_seed"] else (ctx["component"],)
        # 路由前缀：app 有 config.py::URLPATTERNS 时留空（运行期按同一事实源推导，
        # 见 common/core/modules/routes.py）；没有才写显式兜底声明（如内置 app 的
        # 路由挂在 server/urls.py，无可推导来源）。
        routes = () if derive_route_prefixes(ctx["app_label"]) else (f"^/api/{ctx['app_label']}/",)
        return {
            "label": "模块声明",
            "path": Path(options["output"] or settings.PROJECT_DIR) / ctx["app_label"] / "modules.py",
            "content": render_modules_source(
                app_title=app_config.verbose_name or app_config.name,
                module_id=module_id,
                label=module_id,
                level=options["module_level"],
                menus=menus,
                routes=routes,
            ),
            "mode": "create",
            "key": key,
        }

    def _frontend_artifacts(self, ctx, options):
        client_root = Path(options["frontend_root"]) if options["frontend_root"] else self._default_client_root()
        if client_root is None:
            return [
                {
                    "label": "前端页面",
                    "path": None,
                    "content": "".join(
                        [
                            self._render_client_api(ctx),
                            self._render_client_hook(ctx),
                            self._render_client_page(ctx),
                        ]
                    ),
                    "mode": "notice",
                    "key": f"frontend-{ctx['model_snake']}",
                    "notice": "未找到前端仓库根：设置 XADMIN_CLIENT_DIR、传 --frontend-root 或 --skip-frontend"
                    "（以下内容可手工复制）",
                }
            ]
        view_dir = client_root / "src" / "views" / ctx["frontend_dir"]
        return [
            {
                "label": "前端页面",
                "path": view_dir / "index.vue",
                "content": self._render_client_page(ctx),
                "mode": "create",
                "key": f"client-page-{ctx['model_snake']}",
            },
            {
                "label": "前端 API",
                "path": view_dir / "utils" / "api.ts",
                "content": self._render_client_api(ctx),
                "mode": "create",
                "key": f"client-api-{ctx['model_snake']}",
            },
            {
                "label": "前端逻辑",
                "path": view_dir / "utils" / "hook.tsx",
                "content": self._render_client_hook(ctx),
                "mode": "create",
                "key": f"client-hook-{ctx['model_snake']}",
            },
        ]

    @staticmethod
    def _default_client_root():
        """前端仓库根缺省解析：XADMIN_CLIENT_DIR 环境变量优先，其次同级 xadmin-client
        （对齐客户端 sync-contract.mjs 的 XADMIN_SERVER_DIR 覆盖口径）。

        显式设置的环境变量指向不存在目录时不回退同级缺省——按「未找到」降级为
        notice 产物，避免静默写进另一个根。
        """
        env_dir = os.environ.get("XADMIN_CLIENT_DIR")
        if env_dir:
            candidate = Path(env_dir)
            return candidate if candidate.is_dir() else None
        candidate = Path(settings.PROJECT_DIR).parent / "xadmin-client"
        return candidate if candidate.is_dir() else None

    # --------------------------------------------------------------- 后续步骤

    def _bootstrap(self, ctx, options):
        """--bootstrap：种子一条龙幂等入库。

        步骤：sync_model_field（字段权限树）→ 回填种子 model 关联 → loaddata
        菜单/权限点 →（显式 --grant-to 时）授予角色。幂等性：种子 pk 为 uuid5
        确定性生成（loaddata 按主键 upsert）、sync_model_field 幂等、M2M 授权
        幂等——重复执行内容不变。
        """
        if options.get("dry_run"):
            self.stdout.write("--bootstrap 在 --dry-run 下跳过（只打印产物，不落盘）")
            return
        if options.get("skip_menu_seed"):
            self.stdout.write("--bootstrap 跳过（--skip-menu-seed 无种子可入库）")
            return

        from django.core.management import call_command

        # 1) 字段权限树先行：模型节点入库后，种子里的权限点才能关联到 model
        sync_model_field()
        model_pk = self._model_label_pk(ctx["model"])

        seed_path = (
            Path(options["output"] or settings.PROJECT_DIR)
            / "loadjson"
            / f"seed_{ctx['app_label']}_{ctx['model_snake']}.json"
        )
        if model_pk:
            # 回填生成期未解析到的 model 关联（生成先行、同步在后的时序）；
            # 若生成时已解析则种子本就带 pk，此处不产生写入（幂等）
            entries = json.loads(seed_path.read_text(encoding="utf-8"))
            patched = False
            for entry in entries:
                if (
                    entry["model"] == "system.menu"
                    and entry["fields"].get("menu_type") == 2
                    and not entry["fields"].get("model")
                ):
                    entry["fields"]["model"] = [str(model_pk)]
                    patched = True
            if patched:
                seed_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # 2) 菜单与权限点入库（loaddata 幂等 upsert）
        call_command("loaddata", str(seed_path), verbosity=0)
        self.stdout.write(f"--bootstrap: 菜单/权限点已入库（{seed_path.name}）")

        # 3) 角色授权：显式 --grant-to 才执行（授权属业务决策，不自动猜）
        grant_to = [code.strip() for code in (options.get("grant_to") or "").split(",") if code.strip()]
        if not grant_to:
            self.stdout.write("--bootstrap: 未指定 --grant-to，跳过角色授权（菜单管理里手工勾选亦可）")
            return
        permissions = list(PERMISSION_ACTIONS)
        if ctx.get("with_import_export"):
            permissions.extend(IMPORT_EXPORT_PERMISSIONS)
        menu_names = [ctx["component"]] + [f"{action}:{ctx['component']}" for action, _, _ in permissions]
        menus = list(Menu.objects.filter(name__in=menu_names, deleted_at__isnull=True))
        roles = list(UserRole.objects.filter(code__in=grant_to, is_active=True, deleted_at__isnull=True))
        missing_roles = sorted(set(grant_to) - {role.code for role in roles})
        if missing_roles:
            self.stdout.write(self.style.WARNING(f"--bootstrap: 角色不存在或已删除，跳过：{missing_roles}"))
        if menus and roles:
            for role in roles:
                role.menu.add(*menus)
            self.stdout.write(f"--bootstrap: 已授予角色 {[role.code for role in roles]} 共 {len(menus)} 个菜单/权限点")

    def _next_steps(self, ctx, options):
        """后续步骤清单（可复用：CLI 打印 / GUI 生成 NEXT_STEPS.md 同一口径）。

        口径与 docs/guide/first-module-30min.md 同步：权限点种子入库 →
        字段权限树（模型节点缺失时）→ 菜单与授权 → doctor 自检 →（可选）模块声明。
        --bootstrap 已代办的动作从清单中移除。
        """
        bootstrapped = (
            bool(options.get("bootstrap")) and not options.get("dry_run") and not options.get("skip_menu_seed")
        )
        steps = []
        if ctx["app_label"] in (getattr(settings, "XADMIN_APPS", None) or []):
            pass  # 本就注册（或本进程已加载新配置），无需提示
        elif ctx.get("app_registered"):
            steps.append(
                f'应用注册：已由 --register-app 写入 config.yml 的 XADMIN_APPS（含 "{ctx["app_label"]}"），重启进程后生效'
            )
        else:
            steps.append(
                f'应用注册：config.yml 的 XADMIN_APPS 加入 "{ctx["app_label"]}"（或重跑本命令加 --register-app；改后需重启进程）'
            )
        if not options["skip_menu_seed"] and not bootstrapped:
            seed = f"loadjson/seed_{ctx['app_label']}_{ctx['model_snake']}.json"
            steps.append(f"权限点与菜单入库：python manage.py loaddata {seed}")
            if not ctx.get("model_label_pk"):
                steps.append(
                    "字段权限树：python manage.py sync_model_field 后加 --force 重跑本命令"
                    "（模型节点写回种子后字段权限才可用）"
                )
        if not bootstrapped:
            steps.append(f"菜单与授权：菜单管理里挂到目标目录；角色管理勾选 *:{ctx['component']} 权限点")
        steps.append("自检：python manage.py doctor（权限点缺口 / 依赖 / 契约一次看全）")
        if not options["with_module"]:
            steps.append("（可选）声明为可裁剪模块：重跑本命令加 --with-module，或 manage.py generate_module")
        return steps

    def _print_next_steps(self, ctx, options):
        """生成后「后续步骤」清单：把散落教程里的手工动作收敛为可复制命令。"""
        steps = self._next_steps(ctx, options)
        lines = ["", "后续步骤（命令在项目根执行）："] if steps else [""]
        lines.extend(f"  {index}) {text}" for index, text in enumerate(steps, start=1))
        lines.append("")
        lines.append("复核：关联字段 input_type 是否符合数据量（大数据量换 api-search-* 形态）；")
        lines.append("      菜单上级是否要挂到已有目录（--parent 或菜单管理里调整）。")
        self.stdout.write("\n".join(lines))
