#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：AI 动作声明骨架与 pytest 测试骨架模板渲染。

产物模板外部化为同包 ``templates/ai_declarations.tmpl`` 与 ``templates/test_skeleton.tmpl``
（加载 + 填充见 :mod:`devtools.management.commands._generate_crud.templating`）；
RenderMixin 按域拆分（文件行数门禁）的 AI 接入部分，组合与入口见 renderers.py。
"""

from typing import Any

from .constants import BLOCK_END, BLOCK_START
from .templating import render_template


class RenderAiMixin:
    """AI 动作声明与测试骨架渲染。"""

    def _render_ai_declarations(self, ctx: dict[str, Any], options: dict[str, Any]) -> str:
        """AI 动作声明骨架：只读动作直接给出，写动作以注释给出。

        与 ``ai/services/actions/ai_api_actions.py`` 同一格式（``api_action`` 声明式复用
        业务接口）：产物放应用根目录，由 ``ai.services.actions.ai_registry`` 自动发现，
        无需改动 ai 应用。产物过 ``manage.py ai_tool_audit``（端点可发现）与
        ``doctor``（声明路径可解析）。--with-tags 时在产物末尾追加标签白名单声明；
        --with-module 时动作自动标注所属模块（停用模块即从 AI 目录移除）。
        """
        resource = f"/{ctx['url_prefix']}"
        tags_block = ""
        if options.get("with_tags"):
            tags_block = (
                "\n\n# 标签接入（白名单）：把下面的 key 加进 system/models/tag.py::TAGGABLE_MODELS\n"
                f'TAGGABLE_MODEL_KEYS = ["{ctx["app_label"]}.{ctx["model_name"].lower()}"]\n'
            )
        # 声明随功能模块裁剪：仅在生成 modules.py 时标注（未声明模块的应用不受裁剪）
        module_line = ""
        if options.get("with_module"):
            module_id = str(options.get("module_id") or ctx["app_label"])
            module_line = f'\n        modules=("{module_id}",),'
        # 同一应用多模型生成时按生成块合并：每个模型一组 mark 包裹的 dict 条目，
        # 重复生成幂等（--force 只替换本模型的块），不会出现第二个 AI_ACTIONS 覆盖
        block_key = f"ai-actions-{ctx['model_snake']}"
        body = render_template(
            "ai_declarations.tmpl",
            {
                "verbose_name": ctx["verbose_name"],
                "model_snake": ctx["model_snake"],
                "resource": resource,
                "modules": module_line,
                "block_start": f"    {BLOCK_START.format(key=block_key)}",
                "block_end": f"    {BLOCK_END.format(key=block_key)}",
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
