#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：前端三件（api.ts / hook.tsx / index.vue）模板渲染。

产物模板外部化为同包 ``templates/client_*.tmpl``（加载 + 填充见
:mod:`devtools.management.commands._generate_crud.templating`）；RenderMixin
按域拆分（文件行数门禁）的前端部分，组合与入口见 renderers.py。
"""

from typing import Any

from django.core.exceptions import FieldDoesNotExist

from .locale_yaml import merge_locale_yaml
from .templating import render_template


class RenderFrontendMixin:
    """前端模板渲染。"""

    def _render_client_api(self, ctx: dict[str, Any]) -> Any:
        return render_template(
            "client_api.tmpl",
            {
                "verbose_name": ctx["verbose_name"],
                "model_snake": ctx["model_snake"],
                "url_prefix": ctx["url_prefix"],
            },
        )

    def _render_client_hook(self, ctx: dict[str, Any]) -> Any:
        return render_template(
            "client_hook.tmpl",
            {
                "verbose_name": ctx["verbose_name"],
                "model_snake": ctx["model_snake"],
                "component": ctx["component"],
            },
        )

    def _render_client_page(self, ctx: dict[str, Any]) -> Any:
        return render_template(
            "client_page.tmpl",
            {
                "component": ctx["component"],
                "locale_name": ctx["locale_name"],
            },
        )

    @staticmethod
    def _client_locale(ctx: dict[str, Any], locale: str) -> dict[str, Any]:
        """生成词条载荷：菜单标题 + 表格字段 label（zh 取 verbose_name，en 用字段名占位）。

        en 无法从模型元数据推导真实译文，按字段名生成可读占位（``is_active`` →
        ``Is Active``），由开发者按需润色；至少不再把中文当英文展示。
        """
        model = ctx["model"]
        labels: dict[str, str] = {}
        for name in ctx["serializer_fields"]:
            if name == "pk":
                continue
            try:
                field = model._meta.get_field(name)
            except FieldDoesNotExist:
                continue
            labels[name] = str(field.verbose_name) if locale == "zh-CN" else str(name).replace("_", " ").title()
        menu_title = ctx["menu_title"] if locale == "zh-CN" else model._meta.model_name.replace("_", " ").title()
        return {
            "menu_key": ctx["component"],
            "menu_title": menu_title,
            "section": ctx["locale_name"],
            "labels": labels,
        }

    def _render_client_locale(self, ctx: dict[str, Any], locale: str) -> str:
        """dry-run 预览：展示将合并进 locale 文件的片段。"""
        return merge_locale_yaml("", **self._client_locale(ctx, locale))
