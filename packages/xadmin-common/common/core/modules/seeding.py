#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""功能模块注册表：种子文件按模块裁剪。"""

from .gate import _disabled_specs, compute_hidden_menu_pks, permission_prefixes_of


class ModuleSeedFilter:
    """按启用模块过滤种子文件（`load_init_json` 专用）。

    目的：新装库即为「精简形态」——不含停用模块的菜单、权限点与其字段权限绑定，
    与运行期裁剪同一口径（`compute_hidden_menu_pks`），避免"库里全量、界面上隐藏"
    的两套事实。``build()`` 在未配置停用模块时返回 ``None``，调用方走原始种子文件
    （存量部署与全量预设零行为差异）。

    过滤规则：

    - ``system.menu``：剔除隐藏子树；记录剩下菜单引用的 meta；
    - ``system.menumeta``：仅被剔除菜单引用的 meta 一并剔除（原有孤儿 meta 保留）；
    - 其他文件：标量 ``menu`` 外键指向被剔除菜单的行整行剔除（如字段权限），
      列表型 ``menu``（角色绑定、数据权限菜单）中被剔除的主键从列表中移除。
    """

    MENU_MODEL = "system.menu"
    MENU_META_MODEL = "system.menumeta"

    def __init__(self, specs=()) -> None:
        self.specs = tuple(specs)
        self.hidden: frozenset = frozenset()
        self._all_meta_refs: set = set()
        self._kept_meta_refs: set = set()

    @classmethod
    def build(cls):
        """按当前配置构造过滤器；无停用模块时返回 None（调用方走原始种子）。"""

        specs = _disabled_specs()
        return cls(specs) if specs else None

    def compute_hidden(self, menu_rows) -> frozenset:
        """按本过滤器的模块集合计算需剔除的菜单主键（口径与运行期一致）。"""

        rows = [
            (
                row["pk"],
                row["fields"].get("parent"),
                row["fields"].get("menu_type"),
                row["fields"].get("name"),
                row["fields"].get("path"),
            )
            for row in menu_rows
        ]
        return compute_hidden_menu_pks(
            rows,
            names={name for spec in self.specs for name in spec.menus},
            prefixes=permission_prefixes_of(self.specs),
        )

    def filter_rows(self, model_label: str, rows: list) -> list:
        if model_label == self.MENU_MODEL:
            # 懒计算隐藏集合：调用方（种子装配/硬裁剪命令）无需关心调用顺序
            if not self.hidden:
                self.hidden = self.compute_hidden(rows)
            kept = [row for row in rows if row["pk"] not in self.hidden]
            self._all_meta_refs = {row["fields"].get("meta") for row in rows if row["fields"].get("meta")}
            self._kept_meta_refs = {row["fields"].get("meta") for row in kept if row["fields"].get("meta")}
            return kept

        if model_label == self.MENU_META_MODEL:
            dropped = self._all_meta_refs - self._kept_meta_refs
            return [row for row in rows if row["pk"] not in dropped]

        kept = []
        for row in rows:
            fields = row["fields"]
            menu_ref = fields.get("menu")
            # 标量 menu 外键（如字段权限）指向被剔除菜单 → 整行剔除；
            # 列表型 menu（角色/数据权限的 m2m）在下方逐项清理
            if isinstance(menu_ref, str) and menu_ref in self.hidden:
                continue
            for key, value in fields.items():
                # 只处理字符串列表（m2m 主键列表）；rules/layout/schema 等
                # 结构化列表原样保留
                if isinstance(value, list) and any(isinstance(item, str) for item in value):
                    remaining = [item for item in value if not (isinstance(item, str) and item in self.hidden)]
                    if len(remaining) != len(value):
                        fields[key] = remaining
            kept.append(row)
        return kept
