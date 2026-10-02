#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：AI 动作声明骨架与 pytest 测试骨架模板渲染。

RenderMixin 按域拆分（文件行数门禁）的 AI 接入部分；组合与入口见 renderers.py。
"""


class RenderAiMixin:
    """AI 动作声明与测试骨架渲染。"""

    def _render_ai_declarations(self, ctx, options):
        """AI 动作声明骨架：只读动作直接给出，写动作以注释给出。

        与 ``ai/utils/ai_api_registry.py`` 同一格式（``api_action`` 声明式复用
        业务接口）：注册 = 在 registry 里 import 本模块的声明并并入 ``API_ACTION_SPECS``。
        产物过 ``manage.py ai_tool_audit``（端点可发现）与 ``doctor``（声明路径可解析）。
        """
        resource = f"/{ctx['url_prefix']}"
        tags_block = ""
        if options.get("with_tags"):
            tags_block = (
                "\n\n# 标签接入（白名单）：把下面的 key 加进 system/models/tag.py::TAGGABLE_MODELS\n"
                f'TAGGABLE_MODEL_KEYS = ["{ctx["app_label"]}.{ctx["model_name"].lower()}"]\n'
            )
        lines = [
            "#!/usr/bin/env python",
            "# -*- coding:utf-8 -*-",
            f'"""{ctx["verbose_name"]} 的 AI 动作声明骨架（generate_crud 生成）。',
            "",
            "注册方式：在 ai/utils/ai_api_registry.py 里 import 本模块的 AI_READ_ACTIONS，并并入 API_ACTION_SPECS；",
            "只读动作可直接注册（AI 工具目录 / MCP / 原生 function calling 三者同源）。",
            "",
            "写动作（create/update/destroy）启用前需确认：412 审批口径、字段权限、幂等语义，",
            "格式见文件末尾注释。",
            '"""',
            "",
            "from django.utils.translation import gettext_lazy as _",
            "",
            "from ai.utils.ai_api_actions import api_action",
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
        """pytest 测试骨架（`--with-tests`）：鉴权 + 列表契约两条最小断言。"""
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
