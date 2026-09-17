#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""功能模块注册表：模块声明与解析结果的数据结构。"""

from dataclasses import dataclass, field

# 模块等级
CORE = "core"
STANDARD = "standard"
OPTIONAL = "optional"

# 发行预设：等级 → 预设包含关系（core ⊂ standard ⊂ full）
PRESETS = ("core", "standard", "full")
_PRESET_LEVELS = {
    "core": (CORE,),
    "standard": (CORE, STANDARD),
    "full": (CORE, STANDARD, OPTIONAL),
}
DEFAULT_PRESET = "full"

# 菜单行类型（与 system.models.menu.Menu.MenuChoices 一致；此处只按数值判定，
# 避免 common 层在导入期依赖 system 模型）
MENU_TYPE_DIRECTORY = 0
MENU_TYPE_PERMISSION = 2


@dataclass(frozen=True)
class ModuleSpec:
    """一个功能模块的声明。

    :param id: 模块标识（config.yml 中 MODULE_ENABLE/MODULE_DISABLE 使用的名字）
    :param label: 中文名（模块清单文档与后续「模块管理页」展示用）
    :param level: core / standard / optional，决定各预设下是否默认开启
    :param depends: 依赖的模块 id（被依赖模块被关闭而自身开启 → 启动期报错）
    :param menus: 本模块的菜单根 name（含其全部后代菜单与权限点）
    :param permissions: 菜单树覆盖不到的权限点 path 前缀（形如 ``api/system/global-search``）
    :param routes: 请求路径正则前缀，用于路由级 404 拦截
    """

    id: str
    label: str
    level: str = STANDARD
    depends: tuple[str, ...] = ()
    menus: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    routes: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class ModuleResolution:
    """一次模块解析的结果（预设 + 显式覆盖 + 依赖校验后）。"""

    preset: str
    enabled: frozenset
    disabled: frozenset
    overrides: tuple = field(default=())

    @property
    def is_full(self) -> bool:
        """是否为「全部开启」：为真时所有裁剪动作走零开销旁路。"""

        return not self.disabled
