#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：菜单 + 权限种子 JSON 模板渲染。

RenderMixin 按域拆分（文件行数门禁）的种子部分；组合与入口见 renderers.py。
"""

import json
import uuid

from common.contracts import ModelLabelField

from .constants import IMPORT_EXPORT_PERMISSIONS, PERMISSION_ACTIONS, SEED_NAMESPACE


class RenderSeedMixin:
    """菜单种子渲染。"""

    def _render_menu_seed(self, ctx, parent):
        # model_label_pk 由 _collect_artifacts 预先解析（后续步骤提示复用）；兜底现场解析
        model_pk = ctx["model_label_pk"] if "model_label_pk" in ctx else self._model_label_pk(ctx["model"])
        menu_title = ctx.get("menu_title") or ctx["verbose_name"]
        meta_pk = self._seed_pk(ctx, "meta")
        menu_pk = self._seed_pk(ctx, "menu")
        permissions = list(PERMISSION_ACTIONS)
        if ctx["with_import_export"]:
            permissions.extend(IMPORT_EXPORT_PERMISSIONS)
        entries = [
            {
                "model": "system.menumeta",
                "pk": str(meta_pk),
                "fields": {
                    "title": menu_title,
                    "icon": ctx.get("menu_icon") or "ep:document",
                    "r_svg_name": "",
                    "is_show_menu": True,
                    "is_show_parent": False,
                    "is_keepalive": True,
                    "frame_url": "",
                    "frame_loading": False,
                    "transition_enter": "",
                    "transition_leave": "",
                    "is_hidden_tag": False,
                    "fixed_tag": False,
                    "dynamic_level": 0,
                },
            },
            {
                "model": "system.menu",
                "pk": str(menu_pk),
                "fields": {
                    "parent": parent or None,
                    "menu_type": 1,
                    "name": ctx["component"],
                    "rank": 1,
                    "path": f"/{ctx['frontend_dir']}/index",
                    "component": f"{ctx['frontend_dir']}/index",
                    "is_active": True,
                    "meta": str(meta_pk),
                    "method": "",
                    "model": [],
                },
            },
        ]
        for rank, (action, method, path_template) in enumerate(permissions, start=1):
            permission_meta_pk = self._seed_pk(ctx, f"meta-{action}")
            entries.extend(
                [
                    {
                        "model": "system.menu",
                        "pk": str(self._seed_pk(ctx, action)),
                        "fields": {
                            "parent": str(menu_pk),
                            "menu_type": 2,
                            "name": f"{action}:{ctx['component']}",
                            "rank": rank,
                            "path": path_template.format(prefix=ctx["url_prefix"]),
                            "component": None,
                            "is_active": True,
                            "meta": str(permission_meta_pk),
                            "method": method,
                            "model": [str(model_pk)] if model_pk else [],
                        },
                    },
                    {
                        "model": "system.menumeta",
                        "pk": str(permission_meta_pk),
                        "fields": {"title": f"{menu_title}-{action}"},
                    },
                ]
            )
        return json.dumps(entries, ensure_ascii=False, indent=2) + "\n"

    @staticmethod
    def _seed_pk(ctx, role):
        return uuid.uuid5(SEED_NAMESPACE, f"{ctx['app_label']}:{ctx['model_snake']}:{role}")

    @staticmethod
    def _model_label_pk(model):
        """菜单的 model 关联（字段权限数据源）：取 ROLE 树上的模型节点 pk，未同步则为空。"""
        try:
            node = ModelLabelField.objects.filter(
                name=model._meta.label_lower,
                field_type=ModelLabelField.FieldChoices.ROLE,
                parent__isnull=True,
            ).first()
            return node.pk if node else None
        except Exception:  # noqa: BLE001 数据库不可用（未迁移环境/干跑）时降级为空
            return None
