# -*- coding: utf-8 -*-
"""SCIM 协议错误类型（RFC 7644 §3.12 Error 文档的载体）。

独立成模块：resources / guards 都要抛它，放在其中一侧会造成循环导入；
``system.scim.resources`` 继续再导出本类，既有导入路径不变。
"""


class ScimApiError(Exception):
    """SCIM 协议错误：视图统一渲染为 RFC 7644 §3.12 的 Error 文档。"""

    def __init__(self, status: int, detail: str, scim_type: str = ""):
        self.status = status
        self.detail = detail
        self.scim_type = scim_type
        super().__init__(detail)
