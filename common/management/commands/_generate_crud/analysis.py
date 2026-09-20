#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：模型解析、字段规划与产物收集。"""

from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import CommandError
from django.db import models

from .constants import AUDIT_FIELDS, FILE_RELATED_MODEL, SEARCH_EXCLUDE_TYPES


class AnalysisMixin:
    """模型 → 生成上下文与产物清单（不落盘）。"""

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
        ctx = {
            "model": model,
            "app_label": app_label,
            "model_name": model.__name__,
            "model_snake": model_snake,
            "verbose_name": str(model._meta.verbose_name),
            "component": component,
            "router_path": router_path,
            "url_prefix": options["url_prefix"] or f"api/{app_label}/{router_path}",
            "frontend_dir": options["frontend_dir"] or f"{app_label}/{model_snake}",
            "locale_name": component[:1].lower() + component[1:],
            "basename": router_path,
            "with_import_export": options["with_import_export"],
        }
        ctx.update(self._field_plan(model))
        return ctx

    @staticmethod
    def _snake(name):
        out = []
        for index, char in enumerate(name):
            if char.isupper() and index and not name[index - 1].isupper():
                out.append("_")
            out.append(char.lower())
        return "".join(out)

    def _field_plan(self, model):
        """字段映射规则：序列化器字段 / 表格列 / extra_kwargs / 搜索字段。"""
        serializer_fields = ["pk"]
        for field in model._meta.fields:
            if not field.primary_key and field.name not in AUDIT_FIELDS:
                serializer_fields.append(field.name)
        for field in model._meta.many_to_many:
            serializer_fields.append(field.name)

        extra_kwargs = {"pk": {"read_only": True}}
        for field in self._forward_relations(model):
            if field.name in AUDIT_FIELDS:
                continue
            extra_kwargs[field.name] = self._relation_kwargs(field)

        return {
            "serializer_fields": serializer_fields,
            "table_fields": self._table_fields(model),
            "extra_kwargs": extra_kwargs,
            "filter_custom_fields": self._filter_custom_fields(model),
            "filter_meta_fields": self._filter_meta_fields(model),
        }

    @staticmethod
    def _forward_relations(model):
        relations = []
        for field in model._meta.get_fields():
            if not field.is_relation or field.auto_created:
                continue
            if field.many_to_many or field.many_to_one or field.one_to_one:
                relations.append(field)
        return relations

    @staticmethod
    def _relation_kwargs(field):
        related = field.related_model
        if related._meta.label_lower == settings.AUTH_USER_MODEL.lower():
            attrs, fmt, input_type = ["pk", "username"], "{username}({pk})", "api-search-user"
        elif any(item.name == "name" for item in related._meta.fields):
            attrs, fmt, input_type = ["pk", "name"], "{name}({pk})", None
        else:
            attrs, fmt, input_type = ["pk"], "{pk}", None
        kwargs = {"attrs": attrs, "format": fmt}
        if input_type:
            kwargs["input_type"] = input_type
        if field.many_to_many:
            kwargs["required"] = False
        else:
            kwargs["required"] = not field.null and not field.blank
        return kwargs

    @staticmethod
    def _table_fields(model):
        """表格列：关联/choices/布尔/短文本优先，最多 8 列（长文本、JSON、文件不入列）。"""
        buckets = {1: [], 2: [], 3: [], 4: []}
        for field in model._meta.fields:
            if field.primary_key or field.name in AUDIT_FIELDS or field.name in ("created_time", "updated_time"):
                continue
            if isinstance(field, SEARCH_EXCLUDE_TYPES) or isinstance(field, models.TextField):
                continue
            if field.is_relation:
                buckets[1].append(field.name)
            elif field.choices:
                buckets[2].append(field.name)
            elif isinstance(field, models.BooleanField):
                buckets[3].append(field.name)
            elif isinstance(field, models.CharField) and field.max_length <= 128:
                buckets[4].append(field.name)
        for field in model._meta.many_to_many:
            if field.related_model._meta.label_lower != FILE_RELATED_MODEL:
                buckets[1].append(field.name)
        ordered = [name for index in (1, 2, 3, 4) for name in buckets[index]]
        return ["pk"] + ordered[:8]

    @staticmethod
    def _filter_custom_fields(model):
        """搜索自定义过滤器：非 choices 的文本字段走 icontains。"""
        names = []
        for field in model._meta.fields:
            if field.primary_key or field.name in AUDIT_FIELDS or field.choices:
                continue
            if isinstance(field, (models.CharField, models.TextField, models.EmailField, models.SlugField)):
                names.append(field.name)
        return names

    @staticmethod
    def _filter_meta_fields(model):
        """搜索表单字段域：文本/choices/布尔/日期/关联；排除大字段与文件关联。"""
        names = []
        for field in model._meta.fields:
            if field.primary_key or field.name in AUDIT_FIELDS:
                continue
            if isinstance(field, SEARCH_EXCLUDE_TYPES):
                continue
            if field.is_relation and field.related_model._meta.label_lower == FILE_RELATED_MODEL:
                continue
            names.append(field.name)
        for field in model._meta.many_to_many:
            if field.related_model._meta.label_lower != FILE_RELATED_MODEL:
                names.append(field.name)
        return names

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
        from common.core.modules import module_id_conflict, render_modules_source

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
        return {
            "label": "模块声明",
            "path": Path(options["output"] or settings.PROJECT_DIR) / ctx["app_label"] / "modules.py",
            "content": render_modules_source(
                app_title=app_config.verbose_name or app_config.name,
                module_id=module_id,
                label=module_id,
                level=options["module_level"],
                menus=menus,
                routes=(f"^/api/{ctx['app_label']}/",),
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
                    "notice": "未找到前端仓库根：传 --frontend-root 或 --skip-frontend（以下内容可手工复制）",
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
        candidate = Path(settings.PROJECT_DIR).parent / "xadmin-client"
        return candidate if candidate.is_dir() else None

    # --------------------------------------------------------------- 后续步骤

    def _print_next_steps(self, ctx, options):
        """生成后「后续步骤」清单：把散落教程里的手工动作收敛为可复制命令。

        口径与 docs/guide/first-module-30min.md 同步：权限点种子入库 →
        字段权限树（模型节点缺失时）→ 菜单与授权 → doctor 自检 →（可选）模块声明。
        """
        steps = []
        if ctx["app_label"] not in (getattr(settings, "XADMIN_APPS", None) or []):
            steps.append(f'应用注册：config.yml 的 XADMIN_APPS 加入 "{ctx["app_label"]}"（改后需重启进程）')
        if not options["skip_menu_seed"]:
            seed = f"loadjson/seed_{ctx['app_label']}_{ctx['model_snake']}.json"
            steps.append(f"权限点与菜单入库：python manage.py loaddata {seed}")
            if not ctx.get("model_label_pk"):
                steps.append(
                    "字段权限树：python manage.py sync_model_field 后加 --force 重跑本命令"
                    "（模型节点写回种子后字段权限才可用）"
                )
        steps.append(f"菜单与授权：菜单管理里挂到目标目录；角色管理勾选 *:{ctx['component']} 权限点")
        steps.append("自检：python manage.py doctor（权限点缺口 / 依赖 / 契约一次看全）")
        if not options["with_module"]:
            steps.append("（可选）声明为可裁剪模块：重跑本命令加 --with-module，或 manage.py generate_module")

        lines = ["", "后续步骤（命令在项目根执行）："]
        lines.extend(f"  {index}) {text}" for index, text in enumerate(steps, start=1))
        lines.append("")
        lines.append("复核：关联字段 input_type 是否符合数据量（大数据量换 api-search-* 形态）；")
        lines.append("      菜单上级是否要挂到已有目录（--parent 或菜单管理里调整）。")
        self.stdout.write("\n".join(lines))
