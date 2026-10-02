#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：模板渲染组合入口。

RenderMixin 按域拆分（文件行数门禁）：后端四件套见 render_backend.py，
AI 接入见 render_ai.py，前端三件见 render_frontend.py，菜单种子见 render_seed.py；
本模块只做 mixin 组装，保持既有导入面（``from .renderers import RenderMixin``）不变。
"""

from .render_ai import RenderAiMixin
from .render_backend import RenderBackendMixin
from .render_frontend import RenderFrontendMixin
from .render_seed import RenderSeedMixin


class RenderMixin(RenderBackendMixin, RenderAiMixin, RenderFrontendMixin, RenderSeedMixin):
    """把生成上下文渲染为各端源码文本。"""
