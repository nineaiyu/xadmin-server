#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据字典驱动字段的兼容 re-export（实现已下沉框架层 common/core/fields.py）。

DictChoiceField 是通用扩展件（任何业务 app 可用），依赖经
``common.core.fields.register_dict_items_resolver`` 注入（system app 在
AppConfig.ready() 注册 system.utils.dict.get_dict_items），common 零业务依赖。
本模块仅为既有 `from common.core.fields import DictChoiceField`
写法保留兼容别名；新代码请直接从 common.core.fields 导入。
"""

from common.core.fields import DictChoiceField  # noqa: F401

__all__ = ["DictChoiceField"]
