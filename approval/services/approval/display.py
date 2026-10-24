#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""审批处理人显示名快照工具。"""

from typing import Any

from common.utils.display import user_label


def user_display(user: Any) -> str:
    """用户显示名（昵称优先，缺失回落用户名）：写入审批痕迹快照的唯一口径。

    取值逻辑单源于 :func:`common.utils.display.user_label`（内核显示名 API），
    本函数保留为审批域薄门面（多处引用 + 语义审计的域内标签）。
    """
    return str(user_label(user))
