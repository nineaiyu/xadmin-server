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

用法与参数见 `python manage.py generate_crud --help` 与 docs/adr/ADR-027-code-generator.md。
"""

import ast
import json
import uuid
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import models

# 生成块标记：views.py / serializers.py 的幂等合并锚点
BLOCK_START = "# --- xadmin:generated:{key}:start ---"
BLOCK_END = "# --- xadmin:generated:{key}:end ---"
# 菜单种子 pk 的 uuid5 命名空间（固定常量，保证确定性）
SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/nineaiyu/xadmin-server/generated-seed")
# 权限码：动作 → (HTTP 方法, 路径正则)；路径口径与既有种子一致（无前导 ^，$ 收尾）
PERMISSION_ACTIONS = (
    ("list", "GET", "{prefix}$"),
    ("retrieve", "GET", "{prefix}/(?P<pk>[^/.]+)$"),
    ("create", "POST", "{prefix}$"),
    ("partialUpdate", "PATCH", "{prefix}/(?P<pk>[^/.]+)$"),
    ("destroy", "DELETE", "{prefix}/(?P<pk>[^/.]+)$"),
)
IMPORT_EXPORT_PERMISSIONS = (
    ("exportData", "GET", "{prefix}/export-data$"),
    ("importData", "POST", "{prefix}/import-data$"),
)
# 不进序列化器/搜索的字段类型与审计字段名
SEARCH_EXCLUDE_TYPES = (models.JSONField, models.FileField, models.ImageField, models.BinaryField)
AUDIT_FIELDS = ("creator", "modifier", "dept_belong")
FILE_RELATED_MODEL = "system.uploadfile"


class Command(BaseCommand):
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

        if not options["skip_menu_seed"]:
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

    @staticmethod
    def _existing_text(path):
        return path.read_text(encoding="utf-8") if path and path.exists() else ""

    @staticmethod
    def _strip_block(text, key):
        """去掉目标文件中本 key 的旧生成块：块内 import 不算「文件已导入」，--force 替换后不丢 import。"""
        if not text:
            return text
        start = BLOCK_START.format(key=key)
        end = BLOCK_END.format(key=key)
        if start not in text or end not in text:
            return text
        head, _, rest = text.partition(start)
        _, _, tail = rest.partition(end)
        return head.rstrip("\n") + tail

    # ------------------------------------------------------------------ 落盘

    def _emit(self, artifacts, options):
        dry_run = options["dry_run"]
        lines = []
        for item in artifacts:
            if item["mode"] == "notice":
                lines.append(f"[提示] {item['label']}：{item['notice']}")
                if dry_run:
                    lines.append(item["content"])
                continue
            if dry_run:
                lines.append(f"----- {item['label']} → {item['path']} (dry-run) -----")
                lines.append(item["content"])
                continue
            item["path"].parent.mkdir(parents=True, exist_ok=True)
            action = self._write(item, force=options["force"])
            lines.append(f"[{action}] {item['label']} → {item['path']}")
        lines.append(
            "\n提示：生成后请复核两点——关联字段 input_type 是否符合数据量；"
            "菜单是否需要挂到已有目录（--parent 或菜单管理里改上级）。"
        )
        self.stdout.write("\n".join(lines))

    def _write(self, item, force):
        path = item["path"]
        if item["mode"] == "create":
            if path.exists() and not force:
                return "跳过（已存在，--force 覆盖）"
            path.write_text(item["content"], encoding="utf-8")
            return "写入"
        if item["mode"] == "block":
            return self._merge_block(path, item["key"], item["content"], force)
        if item["mode"] == "urls":
            if not path.exists():
                path.write_text(item["content"], encoding="utf-8")
                return "写入"
            return self._merge_urls(path, item)
        raise CommandError(f"未知产物模式：{item['mode']}")

    def _merge_block(self, path, key, content, force):
        """共享文件（views.py / serializers.py）的生成块合并：按标记整块替换，幂等。"""
        start = BLOCK_START.format(key=key)
        end = BLOCK_END.format(key=key)
        # 模块级类之后紧跟注释需两个空行（ruff format 口径），故块尾补足空行
        body = content.rstrip("\n")
        block = f"{start}\n{body}\n\n\n{end}\n"
        if not path.exists():
            # 新建的共享文件同样带标记：后续执行据此识别生成块（幂等前提）
            path.write_text(block, encoding="utf-8")
            return "写入"
        text = path.read_text(encoding="utf-8")
        if start in text and end in text:
            if not force:
                return "跳过（生成块已存在，--force 替换）"
            head, _, rest = text.partition(start)
            _, _, tail = rest.partition(end)
            path.write_text(head + block + tail.lstrip("\n"), encoding="utf-8")
            return "更新生成块"
        # 文件尾部若为模块级定义，标记前需两个空行（ruff format 口径）
        path.write_text(text.rstrip("\n") + "\n\n\n" + block, encoding="utf-8")
        return "追加生成块"

    def _merge_urls(self, path, item):
        """urls.py：顶部补 import、urlpatterns 前插入 router.register 注册行（幂等）。"""
        ctx = item["ctx"]
        text = path.read_text(encoding="utf-8")
        register = f'router.register("{ctx["router_path"]}", {ctx["model_name"]}ViewSet, basename="{ctx["basename"]}")'
        if register in text:
            return "跳过（注册行已存在）"
        if "SimpleRouter" not in text:
            raise CommandError(f"{path} 非 SimpleRouter 形态，请手工注册：{register}")
        import_line = f"from {ctx['view_module']} import {ctx['model_name']}ViewSet"
        lines = text.splitlines()
        if import_line not in lines:
            last_import = max(
                (index for index, line in enumerate(lines) if line.startswith(("import ", "from "))),
                default=-1,
            )
            lines.insert(last_import + 1, import_line)
        insert_at = next((index for index, line in enumerate(lines) if line.startswith("urlpatterns")), len(lines))
        lines.insert(insert_at, register)
        lines.insert(insert_at + 1, "")
        path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
        return "插入注册行"

    # ------------------------------------------------------------ 导入去重工具

    @staticmethod
    def _imported_names(text):
        """目标文件顶层已导入的名字集合（含别名），用于生成块内 import 去重（防 F811）。"""
        names = set()
        if not text:
            return names
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return names
        for node in tree.body:
            if isinstance(node, ast.Import):
                names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.update(alias.asname or alias.name for alias in node.names)
        return names

    def _render_imports(self, specs, existing_names):
        """specs: [(module, [(name, asname), ...])]；已导入的名字跳过，避免重定义。"""
        lines = []
        for module, items in specs:
            missing = [(name, asname) for name, asname in items if (asname or name) not in existing_names]
            if not missing:
                continue
            rendered = ", ".join(f"{name} as {asname}" if asname else name for name, asname in missing)
            lines.append(f"from {module} import {rendered}")
        return lines

    @staticmethod
    def _group_imports(lines):
        """import 行按 第三方 → 框架（common.*） → 应用 分组，组间空行（仓库范式）。"""
        groups = {"third": [], "framework": [], "app": []}
        for line in lines:
            module = line[len("from ") :].split(" import", 1)[0]
            if module.startswith("common"):
                groups["framework"].append(line)
            elif module.startswith(("django", "rest_framework")) or "." not in module:
                groups["third"].append(line)
            else:
                groups["app"].append(line)
        ordered = []
        for key in ("third", "framework", "app"):
            if groups[key]:
                ordered.extend(groups[key])
                ordered.append("")
        return ordered[:-1] if ordered else ordered

    # ------------------------------------------------------------ Python 模板

    @staticmethod
    def _module_header(ctx, standalone, title, note):
        """模块头部：独立文件带 shebang/docstring；追加进共享文件的生成块用注释头。"""
        if not standalone:
            return [f"# {ctx['verbose_name']} {title}（generate_crud 生成）：{note}", ""]
        return [
            "#!/usr/bin/env python",
            "# -*- coding:utf-8 -*-",
            f'"""{ctx["verbose_name"]} {title}（generate_crud 生成）。',
            "",
            note,
            '"""',
            "",
        ]

    def _render_serializer_module(self, ctx, existing, standalone):
        imports = self._render_imports(
            (
                ("common.core.serializers", [("BaseModelSerializer", None)]),
                (ctx["app_label"], [("models", None)]),
            ),
            self._imported_names(existing),
        )
        lines = [
            *self._module_header(
                ctx,
                standalone=standalone,
                title="序列化器",
                note="字段声明同时驱动 search-columns 元数据与前端渲染：增删字段先想清楚三层影响"
                "（元数据 / 权限码关联模型 / 前端列），参考 docs/architecture/framework-cookbook.md。",
            ),
            *self._group_imports(imports),
            "",
            "",
            f"class {ctx['model_name']}Serializer(BaseModelSerializer):",
            "    class Meta:",
            f"        model = models.{ctx['model_name']}",
            "        fields = [",
            *[f'            "{name}",' for name in ctx["serializer_fields"]],
            "        ]",
            "        table_fields = [",
            *[f'            "{name}",' for name in ctx["table_fields"]],
            "        ]",
            "        # 关联字段形态：attrs 至少含 pk；数据量大时按 cookbook 换 input_type",
            "        extra_kwargs = {",
            *[self._render_kwargs(name, kwargs) for name, kwargs in ctx["extra_kwargs"].items()],
            "        }",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def _render_kwargs(name, kwargs):
        """extra_kwargs 条目：逐键展开 + magic trailing comma（ruff format 幂等）。"""
        lines = [f'            "{name}": {{']
        for key, value in kwargs.items():
            lines.append(f'                "{key}": {Command._py_value(value)},')
        lines.append("            },")
        return "\n".join(lines)

    @staticmethod
    def _py_value(value):
        if isinstance(value, list):
            return "[" + ", ".join(f'"{item}"' for item in value) + "]"
        if isinstance(value, bool):
            return "True" if value else "False"
        return f'"{value}"'

    def _render_views_module(self, ctx, existing, standalone):
        specs = [
            ("django_filters", [("rest_framework", "filters")]),
            ("common.core.filter", [("BaseFilterSet", None)]),
            ("common.core.modelset", [("BaseModelSet", None)]),
        ]
        if ctx["with_import_export"]:
            specs.append(("common.core.modelset", [("ImportExportDataAction", None)]))
        specs.extend(
            [
                (f"{ctx['app_label']}.models", [(ctx["model_name"], None)]),
                (ctx["serializer_import"], [(f"{ctx['model_name']}Serializer", None)]),
            ]
        )
        imports = self._render_imports(specs, self._imported_names(existing))
        mixins = "BaseModelSet, ImportExportDataAction" if ctx["with_import_export"] else "BaseModelSet"
        lines = [
            *self._module_header(
                ctx,
                standalone=standalone,
                title="视图",
                note="数据权限由 BaseViewSet.get_queryset/filter_queryset 全局挂载，勿绕过；"
                "自定义 action 的 docstring 必写（菜单与访问日志显示名取自它）。",
            ),
            *self._group_imports(imports),
            "",
            "",
            f"class {ctx['model_name']}ViewSetFilter(BaseFilterSet):",
            *[
                f'    {name} = filters.CharFilter(field_name="{name}", lookup_expr="icontains")'
                for name in ctx["filter_custom_fields"]
            ],
            "",
            "    class Meta:",
            f"        model = {ctx['model_name']}",
            "        fields = [",
            *[f'            "{name}",' for name in ctx["filter_meta_fields"]],
            "        ]",
            "",
            "",
            f"class {ctx['model_name']}ViewSet({mixins}):",
            f'    """{ctx["verbose_name"]}"""',
            "",
            f"    queryset = {ctx['model_name']}.objects.all()",
            f"    serializer_class = {ctx['model_name']}Serializer",
            '    ordering_fields = ["created_time"]',
            f"    filterset_class = {ctx['model_name']}ViewSetFilter",
            "",
        ]
        return "\n".join(lines)

    def _render_urls_module(self, ctx):
        lines = [
            "#!/usr/bin/env python",
            "# -*- coding:utf-8 -*-",
            f'"""{ctx["verbose_name"]} 路由（generate_crud 生成）。"""',
            "",
            "from rest_framework.routers import SimpleRouter",
            "",
            f"from {ctx['view_module']} import {ctx['model_name']}ViewSet",
            "",
            f'app_name = "{ctx["app_label"]}"',
            "",
            "router = SimpleRouter(False)  # 设置为 False ,为了去掉url后面的斜线",
            "",
            f'router.register("{ctx["router_path"]}", {ctx["model_name"]}ViewSet, basename="{ctx["basename"]}")',
            "",
            "urlpatterns = []",
            "urlpatterns += router.urls",
            "",
        ]
        return "\n".join(lines)

    def _render_config(self, ctx):
        app_label = ctx["app_label"]
        lines = [
            "#!/usr/bin/env python",
            "# -*- coding:utf-8 -*-",
            f'"""{app_label} 应用配置（generate_crud 生成）。"""',
            "",
            "from django.urls import include, path",
            "",
            "# 路由配置，当添加APP完成时候，会自动注入路由到总服务",
            "URLPATTERNS = [",
            f'    path("api/{app_label}/", include("{app_label}.urls")),',
            "]",
            "",
            "# 请求白名单，支持正则表达式，可参考 settings 的 PERMISSION_WHITE_URL",
            "PERMISSION_WHITE_REURL = []",
            "",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------ 前端模板

    def _render_client_api(self, ctx):
        lines = [
            'import { BaseApi } from "@/api/base";',
            "",
            f"/** {ctx['verbose_name']}（generate_crud 生成）",
            " *",
            " * 自定义 action 用子类方法追加，参考 src/api/system/task.ts。",
            " */",
            f'export const {ctx["model_snake"]}Api = new BaseApi("/{ctx["url_prefix"]}");',
            "",
        ]
        return "\n".join(lines)

    def _render_client_hook(self, ctx):
        lines = [
            'import { getCurrentInstance, reactive } from "vue";',
            'import { getDefaultAuths } from "@/router/utils";',
            "",
            f'import {{ {ctx["model_snake"]}Api }} from "./api";',
            "",
            f"/** {ctx['verbose_name']} 页面逻辑（generate_crud 生成）",
            " *",
            " * 表格列/搜索/表单覆写按需追加：listColumnsFormat / searchColumnsFormat /",
            " * addOrEditOptions，参考 xadmin-docs example/new-app-client.md。",
            " */",
            f"export function use{ctx['component']}() {{",
            f"  const api = reactive({ctx['model_snake']}Api);",
            "  const auth = reactive({ ...getDefaultAuths(getCurrentInstance()) });",
            "",
            "  return { api, auth };",
            "}",
            "",
        ]
        return "\n".join(lines)

    def _render_client_page(self, ctx):
        lines = [
            '<script lang="ts" setup>',
            'import { RePlusPage } from "@/components/RePlusPage";',
            "",
            f'import {{ use{ctx["component"]} }} from "./utils/hook";',
            "",
            "defineOptions({",
            "  // 必须唯一：权限码按 动作:组件名 匹配",
            f'  name: "{ctx["component"]}"',
            "});",
            "",
            f"const {{ api, auth }} = use{ctx['component']}();",
            "</script>",
            "",
            "<template>",
            f'  <RePlusPage :api="api" :auth="auth" locale-name="{ctx["locale_name"]}" />',
            "</template>",
            "",
        ]
        return "\n".join(lines)

    # ----------------------------------------------------------------- 菜单种子

    def _render_menu_seed(self, ctx, parent):
        model_pk = self._model_label_pk(ctx["model"])
        meta_pk = self._seed_pk(ctx, "meta")
        menu_pk = self._seed_pk(ctx, "menu")
        permissions = list(PERMISSION_ACTIONS)
        if ctx["with_import_export"]:
            permissions.extend(IMPORT_EXPORT_PERMISSIONS)
        entries = [
            {
                "model": "system.menumeta",
                "pk": str(meta_pk),
                "fields": {
                    "title": ctx["verbose_name"],
                    "icon": "ep:document",
                    "r_svg_name": "",
                    "is_show_menu": True,
                    "is_show_parent": False,
                    "is_keepalive": True,
                    "frame_url": "",
                    "frame_loading": False,
                    "transition_enter": "",
                    "transition_leave": "",
                    "is_hidden_tag": False,
                    "fixed_tag": False,
                    "dynamic_level": 0,
                },
            },
            {
                "model": "system.menu",
                "pk": str(menu_pk),
                "fields": {
                    "parent": parent or None,
                    "menu_type": 1,
                    "name": ctx["component"],
                    "rank": 1,
                    "path": f"/{ctx['frontend_dir']}/index",
                    "component": f"{ctx['frontend_dir']}/index",
                    "is_active": True,
                    "meta": str(meta_pk),
                    "method": "",
                    "model": [],
                },
            },
        ]
        for rank, (action, method, path_template) in enumerate(permissions, start=1):
            permission_meta_pk = self._seed_pk(ctx, f"meta-{action}")
            entries.extend(
                [
                    {
                        "model": "system.menu",
                        "pk": str(self._seed_pk(ctx, action)),
                        "fields": {
                            "parent": str(menu_pk),
                            "menu_type": 2,
                            "name": f"{action}:{ctx['component']}",
                            "rank": rank,
                            "path": path_template.format(prefix=ctx["url_prefix"]),
                            "component": None,
                            "is_active": True,
                            "meta": str(permission_meta_pk),
                            "method": method,
                            "model": [str(model_pk)] if model_pk else [],
                        },
                    },
                    {
                        "model": "system.menumeta",
                        "pk": str(permission_meta_pk),
                        "fields": {"title": f"{ctx['verbose_name']}-{action}"},
                    },
                ]
            )
        return json.dumps(entries, ensure_ascii=False, indent=2) + "\n"

    @staticmethod
    def _seed_pk(ctx, role):
        return uuid.uuid5(SEED_NAMESPACE, f"{ctx['app_label']}:{ctx['model_snake']}:{role}")

    @staticmethod
    def _model_label_pk(model):
        """菜单的 model 关联（字段权限数据源）：取 ROLE 树上的模型节点 pk，未同步则为空。"""
        try:
            from system.models import ModelLabelField

            node = ModelLabelField.objects.filter(
                name=model._meta.label_lower,
                field_type=ModelLabelField.FieldChoices.ROLE,
                parent__isnull=True,
            ).first()
            return node.pk if node else None
        except Exception:  # noqa: BLE001 数据库不可用（未迁移环境/干跑）时降级为空
            return None
