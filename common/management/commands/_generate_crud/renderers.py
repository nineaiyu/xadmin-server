#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：后端/前端/菜单种子模板渲染。"""

import json
import uuid

from .constants import IMPORT_EXPORT_PERMISSIONS, PERMISSION_ACTIONS, SEED_NAMESPACE


class RenderMixin:
    """把生成上下文渲染为各端源码文本。"""

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
            lines.append(f'                "{key}": {RenderMixin._py_value(value)},')
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

    # ------------------------------------------------------- AI 接入（E-3）

    def _render_ai_declarations(self, ctx, options):
        """AI 动作声明骨架（E-3）：只读动作直接给出，写动作以注释给出。

        与 ``system/utils/ai_api_registry.py`` 同一格式（``api_action`` 声明式复用
        业务接口）：注册 = 在 registry 里 import 本模块的声明并并入 ``API_ACTION_SPECS``。
        产物过 ``manage.py ai_tool_audit``（端点可发现）与 ``doctor``（声明路径可解析）。
        """
        resource = f"/{ctx['url_prefix']}"
        tags_block = ""
        if options.get("with_tags"):
            tags_block = (
                "\n\n# 标签接入（P-1 白名单）：把下面的 key 加进 system/models/tag.py::TAGGABLE_MODELS\n"
                f'TAGGABLE_MODEL_KEYS = ["{ctx["app_label"]}.{ctx["model_name"].lower()}"]\n'
            )
        lines = [
            "#!/usr/bin/env python",
            "# -*- coding:utf-8 -*-",
            f'"""{ctx["verbose_name"]} 的 AI 动作声明骨架（generate_crud 生成）。',
            "",
            "注册方式：在 system/utils/ai_api_registry.py 里 import 本模块的 AI_READ_ACTIONS，"
            "并并入 API_ACTION_SPECS；",
            "只读动作可直接注册（AI 工具目录 / MCP / 原生 function calling 三者同源）。",
            "",
            "写动作（create/update/destroy）启用前需确认：412 审批口径、字段权限、幂等语义，",
            "格式见文件末尾注释。",
            '"""',
            "",
            "from django.utils.translation import gettext_lazy as _",
            "",
            "from system.utils.ai_api_actions import api_action",
            "",
            f'RESOURCE_PATH = "{resource}"',
            "",
            "AI_READ_ACTIONS = {",
            f'    "{ctx["model_snake"]}.list": api_action(',
            f'        key="{ctx["model_snake"]}.list",',
            f'        label=_("{ctx["verbose_name"]}列表"),',
            f'        description=_("查询{ctx["verbose_name"]}列表（支持关键字与分页）"),',
            '        method="GET",',
            "        path=RESOURCE_PATH,",
            "        params={",
            '            "search": {"type": "string", "in": "query", "description": "关键字"},',
            '            "page": {"type": "int", "in": "query", "description": "页码"},',
            '            "size": {"type": "int", "in": "query", "description": "每页条数"},',
            "        },",
            "    ),",
            f'    "{ctx["model_snake"]}.detail": api_action(',
            f'        key="{ctx["model_snake"]}.detail",',
            f'        label=_("{ctx["verbose_name"]}详情"),',
            f'        description=_("按主键查询单条{ctx["verbose_name"]}"),',
            '        method="GET",',
            '        path=f"{RESOURCE_PATH}/<pk>",',
            "        params={",
            '            "pk": {"type": "pk", "in": "path", "required": True, "description": "主键"},',
            "        },",
            "    ),",
            "}",
            "",
            "# 写动作（确认审批与权限后放开）：",
            "# AI_WRITE_ACTIONS = {",
            f'#     "{ctx["model_snake"]}.create": api_action(',
            f'#         key="{ctx["model_snake"]}.create", label=_("新建{ctx["verbose_name"]}"),',
            f'#         description=_("新建一条{ctx["verbose_name"]}"), method="POST", path=RESOURCE_PATH,',
            "#         params={},  # 按模型字段补齐（必填字段用 required=True）",
            "#     ),",
            "# }",
        ]
        return ("\n".join(lines) + tags_block).rstrip("\n") + "\n"

    def _render_test_skeleton(self, ctx):
        """pytest 测试骨架（E-3，`--with-tests`）：鉴权 + 列表契约两条最小断言。"""
        return "\n".join(
            [
                "# -*- coding: utf-8 -*-",
                f'"""{ctx["verbose_name"]} API 测试骨架（generate_crud 生成）：按需补断言后提交。"""',
                "",
                "import pytest",
                "",
                "pytestmark = pytest.mark.django_db",
                "",
                f'LIST_URL = "/{ctx["url_prefix"]}"',
                "",
                "",
                f"class Test{ctx['model_name']}Api:",
                "    def test_list_requires_auth(self, api_client):",
                "        assert api_client.get(LIST_URL).status_code == 401",
                "",
                "    def test_list_contract(self, auth_client):",
                '        response = auth_client.get(LIST_URL, {"page": 1, "size": 10})',
                "        assert response.status_code == 200",
                '        assert "results" in response.json()["data"]',
                "",
            ]
        )

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
        # model_label_pk 由 _collect_artifacts 预先解析（后续步骤提示复用）；兜底现场解析
        model_pk = ctx["model_label_pk"] if "model_label_pk" in ctx else self._model_label_pk(ctx["model"])
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
