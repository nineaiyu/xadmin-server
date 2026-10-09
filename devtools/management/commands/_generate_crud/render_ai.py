#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：AI 动作声明骨架与 pytest 测试骨架模板渲染。

产物模板外部化为同包 ``templates/ai_declarations.tmpl`` 与 ``templates/test_skeleton.tmpl``
（加载 + 填充见 :mod:`devtools.management.commands._generate_crud.templating`）；
RenderMixin 按域拆分（文件行数门禁）的 AI 接入部分，组合与入口见 renderers.py。
"""

from typing import Any

from .templating import render_template


class RenderAiMixin:
    """AI 动作声明与测试骨架渲染。"""

    def _render_ai_declarations(self, ctx: dict[str, Any], options: dict[str, Any]) -> str:
        """AI 动作声明骨架：只读动作直接给出，写动作以注释给出。

        与 ``ai/utils/ai_api_registry.py`` 同一格式（``api_action`` 声明式复用
        业务接口）：注册 = 在 registry 里 import 本模块的声明并并入 ``API_ACTION_SPECS``。
        产物过 ``manage.py ai_tool_audit``（端点可发现）与 ``doctor``（声明路径可解析）。
        --with-tags 时在产物末尾追加标签白名单声明。
        """
        resource = f"/{ctx['url_prefix']}"
        tags_block = ""
        if options.get("with_tags"):
            tags_block = (
                "\n\n# 标签接入（白名单）：把下面的 key 加进 system/models/tag.py::TAGGABLE_MODELS\n"
                f'TAGGABLE_MODEL_KEYS = ["{ctx["app_label"]}.{ctx["model_name"].lower()}"]\n'
            )
        body = render_template(
            "ai_declarations.tmpl",
            {
                "verbose_name": ctx["verbose_name"],
                "model_snake": ctx["model_snake"],
                "resource": resource,
            },
        )
        # 模板以换行结尾，产物体不含末尾空行：先收尾再拼标签块，保持与历史逐字节一致
        return (body.rstrip("\n") + tags_block).rstrip("\n") + "\n"

    def _render_test_skeleton(self, ctx: dict[str, Any]) -> str:
        """pytest 测试骨架（`--with-tests`）：鉴权 + 列表契约两条最小断言。"""
        return render_template(
            "test_skeleton.tmpl",
            {
                "verbose_name": ctx["verbose_name"],
                "model_name": ctx["model_name"],
                "url_prefix": ctx["url_prefix"],
            },
        )
