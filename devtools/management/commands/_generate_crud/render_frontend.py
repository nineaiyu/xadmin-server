#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：前端三件（api.ts / hook.tsx / index.vue）模板渲染。

产物模板外部化为同包 ``templates/client_*.tmpl``（加载 + 填充见
:mod:`devtools.management.commands._generate_crud.templating`）；RenderMixin
按域拆分（文件行数门禁）的前端部分，组合与入口见 renderers.py。
"""

from .templating import render_template


class RenderFrontendMixin:
    """前端模板渲染。"""

    def _render_client_api(self, ctx):
        return render_template(
            "client_api.tmpl",
            {
                "verbose_name": ctx["verbose_name"],
                "model_snake": ctx["model_snake"],
                "url_prefix": ctx["url_prefix"],
            },
        )

    def _render_client_hook(self, ctx):
        return render_template(
            "client_hook.tmpl",
            {
                "verbose_name": ctx["verbose_name"],
                "model_snake": ctx["model_snake"],
                "component": ctx["component"],
            },
        )

    def _render_client_page(self, ctx):
        return render_template(
            "client_page.tmpl",
            {
                "component": ctx["component"],
                "locale_name": ctx["locale_name"],
            },
        )
