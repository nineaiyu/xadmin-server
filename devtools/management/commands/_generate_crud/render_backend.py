#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：后端四件套（serializers / views / urls / config）模板渲染。

RenderMixin 按域拆分（文件行数门禁）的后端部分；组合与入口见 renderers.py。
"""

from typing import TYPE_CHECKING


class RenderBackendMixin:
    """后端四件套模板渲染。"""

    if TYPE_CHECKING:  # 组合使用的兄弟 mixin（MergeMixin）提供（mixin 模式）

        def _render_imports(self, specs, existing_names) -> list[str]: ...

        def _imported_names(self, text) -> set[str]: ...

        def _group_imports(self, lines, extra_first_party=frozenset()) -> list[str]: ...

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
        dict_fields = ctx.get("dict_fields") or {}
        specs = [
            ("common.core.serializers", [("BaseModelSerializer", None)]),
            (ctx["app_label"], [("models", None)]),
        ]
        if dict_fields:
            specs.append(("common.core.fields_dict", [("DictChoiceField", None)]))
        imports = self._render_imports(specs, self._imported_names(existing))
        lines = [
            *self._module_header(
                ctx,
                standalone=standalone,
                title="序列化器",
                note="字段声明同时驱动 search-columns 元数据与前端渲染：增删字段先想清楚三层影响"
                "（元数据 / 权限码关联模型 / 前端列），参考 docs/architecture/framework-cookbook.md。",
            ),
            *self._group_imports(imports, {ctx["app_label"]}),
            "",
            "",
            f"class {ctx['model_name']}Serializer(BaseModelSerializer):",
            *self._render_dict_declarations(ctx, dict_fields),
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
    def _render_dict_declarations(ctx, dict_fields):
        """字典绑定字段的显式声明：DictChoiceField(dict_code=...)，整型值带 value_cast=int。

        显式声明放在 class 体首、Meta 之前（与 identity/serializers/user.py 的 gender 同范式）；
        空绑定返回空列表（产物与未启用字典时逐字节一致，保证幂等）。
        """
        if not dict_fields:
            return []
        model = ctx["model"]
        integer_types = {
            "IntegerField",
            "SmallIntegerField",
            "BigIntegerField",
            "PositiveIntegerField",
            "PositiveSmallIntegerField",
            "PositiveBigIntegerField",
        }
        lines = []
        for name, code in dict_fields.items():
            field = next((item for item in model._meta.fields if item.name == name), None)
            cast = ", value_cast=int" if field is not None and field.get_internal_type() in integer_types else ""
            lines.append(f'    {name} = DictChoiceField(dict_code="{code}"{cast})')
        lines.append("")
        return lines

    @staticmethod
    def _render_kwargs(name, kwargs):
        """extra_kwargs 条目：逐键展开 + magic trailing comma（ruff format 幂等）。"""
        lines = [f'            "{name}": {{']
        for key, value in kwargs.items():
            lines.append(f'                "{key}": {RenderBackendMixin._py_value(value)},')
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
            *self._group_imports(imports, {ctx["app_label"]}),
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
            *([f'    ordering = ["{ctx["default_ordering"]}"]'] if ctx.get("default_ordering") else []),
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
