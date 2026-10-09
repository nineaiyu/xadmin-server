# -*- coding: utf-8 -*-
"""跨仓一致性治理：工作区级只读健康检查。

本包按「同源面」组织——每个面校验同一份事实在多个仓库中的副本是否一致
（CSP 策略 / 权限种子 / 契约 schema / 国际化词条 / 版本矩阵 / 单仓门禁），
全部只读：只读取被检查文件，绝不改写。入口见 ``scripts/workspace_health.py``。
"""

__all__ = ["discovery", "report", "textutil", "procs"]
