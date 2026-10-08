#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : exceptions
# author : ly_13
# date : 8/6/2024
"""验证码域异常：定义已下沉框架层（common），此处仅再导出保持既有导入面（类型同一）。"""

from common.utils.verify_code import (
    CodeError,
    CodeExpired,
    CodeSendOverRate,
    CodeSendTooFrequently,
)

__all__ = [
    "CodeError",
    "CodeExpired",
    "CodeSendOverRate",
    "CodeSendTooFrequently",
]
