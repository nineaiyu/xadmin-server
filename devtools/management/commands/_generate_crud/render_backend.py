#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：后端四件套（serializers / views / urls / config）模板渲染。

产物模板外部化为同包 ``templates/backend_*.tmpl``（加载 + 填充见
:mod:`devtools.management.commands._generate_crud.templating`）；RenderMixin
按域拆分（文件行数门禁）的后端部分，组合与入口见 renderers.py。
"""

from typing import TYPE_CHECKING

from .templating import render_template


class RenderBackendMixin:
    """后端四件套模板渲染。"""

    if TYPE_CHECKING:  # 组合使用的兄弟 mixin（MergeMixin）提供（mixin 模式）

        def _render_imports(self, specs, existing_names) -> list[str]: ...

        def _imported_names(self, text) -> set[str]: ...

        def _group_imports(self, lines, extra_first_party=frozenset()) -> list[str]: ...

    # ------------------------------------------------------------ 公共片段

    @staticmethod
    def _module_header(ctx, standalone, title, note):
        """模块头部：独立文件带 shebang/docstring；追加进共享文件的生成块用注释头。

        头部随「独立文件 / 生成块」两种落盘形态切换（条件片段，故留在渲染侧），
        作为 ``[[header]]`` 行块填充进各产物模板。
        """
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

    # ------------------------------------------------------------ 产物模板

    def _render_serializer_module(self, ctx, existing, standalone):
        dict_fields = ctx.get("dict_fields") or {}
        specs = [
            ("common.core.serializers", [("BaseModelSerializer", None)]),
            (ctx["app_label"], [("models", None)]),
        ]
        if dict_fields:
            specs.append(("common.core.fields_dict", [("DictChoiceField", None)]))
        imports = self._render_imports(specs, self._imported_names(existing))
        return render_template(
            "backend_serializer.tmpl",
            {
                "header": self._module_header(
                    ctx,
                    standalone=standalone,
                    title="序列化器",
                    note="字段声明同时驱动 search-columns 元数据与前端渲染：增删字段先想清楚三层影响"
                    "（元数据 / 权限码关联模型 / 前端列），参考 docs/architecture/framework-cookbook.md。",
                ),
                "imports": self._group_imports(imports, {ctx["app_label"]}),
                "model_name": ctx["model_name"],
                "dict_declarations": self._render_dict_declarations(ctx, dict_fields),
                "serializer_fields": [f'            "{name}",' for name in ctx["serializer_fields"]],
                "table_fields": [f'            "{name}",' for name in ctx["table_fields"]],
                "extra_kwargs": [self._render_kwargs(name, kwargs) for name, kwargs in ctx["extra_kwargs"].items()],
            },
        )

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
        ordering = ctx.get("default_ordering")
        return render_template(
            "backend_views.tmpl",
            {
                "header": self._module_header(
                    ctx,
                    standalone=standalone,
                    title="视图",
                    note="数据权限由 BaseViewSet.get_queryset/filter_queryset 全局挂载，勿绕过；"
                    "自定义 action 的 docstring 必写（菜单与访问日志显示名取自它）。",
                ),
                "imports": self._group_imports(imports, {ctx["app_label"]}),
                "model_name": ctx["model_name"],
                "mixins": mixins,
                "verbose_name": ctx["verbose_name"],
                "filter_custom_fields": [
                    f'    {name} = filters.CharFilter(field_name="{name}", lookup_expr="icontains")'
                    for name in ctx["filter_custom_fields"]
                ],
                "filter_meta_fields": [f'            "{name}",' for name in ctx["filter_meta_fields"]],
                "default_ordering": [f'    ordering = ["{ordering}"]'] if ordering else [],
            },
        )

    def _render_urls_module(self, ctx):
        return render_template(
            "backend_urls.tmpl",
            {
                "verbose_name": ctx["verbose_name"],
                "view_module": ctx["view_module"],
                "model_name": ctx["model_name"],
                "app_label": ctx["app_label"],
                "router_path": ctx["router_path"],
                "basename": ctx["basename"],
            },
        )

    def _render_config(self, ctx):
        return render_template("backend_config.tmpl", {"app_label": ctx["app_label"]})
