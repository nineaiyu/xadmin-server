#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：应用种子产物与「一条龙入库」（按域拆分的 mixin）。

种子已按应用分层（ADR-095），生成器因此做两件事：

1. **产物落到本应用** ``<app>/seeds/``：文件名与分层工具一致（``menumeta.json`` /
   ``menu.json``），按主键合并后登记进同目录 ``manifest.json``——不再往平台层
   ``loadjson/`` 塞文件，也不产生与 ``seed_split`` 重名冲突的 ``menu_<model>.json``；
2. **``--bootstrap`` 一条龙**：字段权限树 → 回填 model 关联 → loaddata →（显式
   ``--grant-to``）角色授权，全链路幂等。
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.management.base import CommandError

from common.contracts import Menu, UserRole, sync_model_field

from .constants import IMPORT_EXPORT_PERMISSIONS, PERMISSION_ACTIONS

# 种子层内顺序：菜单元数据先于菜单（菜单 OneToOne 引用 meta）
_SEED_FILES: tuple[tuple[str, str, int], ...] = (
    ("system.menumeta", "menumeta", 10),
    ("system.menu", "menu", 20),
)


class SeedIngestMixin:
    """菜单种子产物收集与入库（宿主 Command 提供 stdout / style / _model_label_pk 等）。"""

    if TYPE_CHECKING:  # 宿主 Command 提供的接口（mixin 模式）
        stdout: Any
        style: Any

        def _model_label_pk(self, model: Any) -> Any: ...

        def _render_menu_seed(self, ctx: dict[str, Any], parent: Any) -> str: ...

    def _menu_seed_artifacts(
        self, ctx: dict[str, Any], options: dict[str, Any], backend_root: Path
    ) -> list[dict[str, Any]]:
        """菜单/权限种子产物：落进**本应用**的 ``seeds/``，文件名与分层工具一致。

        不能再往平台层 ``loadjson/`` 塞：那会让新模块的菜单依赖共享文件，也绕过归属校验
        （一个文件只登记一个模型）。同一模型的所有生成产物合并进同一个规范文件
        （``menu.json`` / ``menumeta.json``，按主键 upsert），避免与 ``seed_split`` /
        ``dump_init_json --split-apps`` 的落点不一致产生「同层同主键两份」。

        已存在按模型拆分的旧文件（``menu_<model>.json``）时沿用其文件名，保证老项目
        重复生成不产生第二份。
        """
        ctx["model_label_pk"] = self._model_label_pk(ctx["model"])
        entries = json.loads(self._render_menu_seed(ctx, options["parent"]))
        seed_dir = backend_root / ctx["app_label"] / "seeds"
        by_model: dict[str, list[Any]] = {}
        for entry in entries:
            by_model.setdefault(entry["model"], []).append(entry)

        artifacts: list[dict[str, Any]] = []
        manifest_files: list[dict[str, Any]] = []
        for model, stem, order in _SEED_FILES:
            rows = by_model.get(model)
            if not rows:
                continue
            filename = _seed_target_name(seed_dir, model, f"{stem}.json")
            artifacts.append(
                {
                    "label": f"菜单种子（{stem}）",
                    "path": seed_dir / filename,
                    "content": json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                    "mode": "seed_rows",
                    "key": f"seed-{model}-{ctx['model_snake']}",
                }
            )
            manifest_files.append({"name": filename, "model": model, "kind": "contract", "order": order})

        if manifest_files:
            artifacts.append(
                {
                    "label": "应用种子清单",
                    "path": seed_dir / "manifest.json",
                    "mode": "seed_manifest",
                    "content": json.dumps(manifest_files, ensure_ascii=False, indent=2),
                    "app": ctx["app_label"],
                    "depends": ["system", "identity"],
                    "url_prefixes": [f"{ctx['url_prefix'].rstrip('/')}/"],
                    "files": manifest_files,
                }
            )
        return artifacts

    # ------------------------------------------------------------------ 入库

    def _bootstrap(self, ctx: dict[str, Any], options: dict[str, Any]) -> None:
        """``--bootstrap``：种子一条龙幂等入库。

        步骤：``sync_model_field``（字段权限树）→ 回填种子 model 关联 → ``loaddata``
        菜单/权限点 →（显式 ``--grant-to`` 时）授予角色。幂等性：种子 pk 为 uuid5
        确定性生成（loaddata 按主键 upsert）、``sync_model_field`` 幂等、M2M 授权幂等。
        """
        if options.get("dry_run"):
            self.stdout.write("--bootstrap 在 --dry-run 下跳过（只打印产物，不落盘）")
            return
        if options.get("skip_menu_seed"):
            self.stdout.write("--bootstrap 跳过（--skip-menu-seed 无种子可入库）")
            return

        from django.core.management import call_command

        # 1) 字段权限树先行：模型节点入库后，种子里的权限点才能关联到 model
        sync_model_field()
        model_pk = self._model_label_pk(ctx["model"])

        seed_paths = self._seed_paths(ctx, options)
        if model_pk:
            self._backfill_model_links(seed_paths, model_pk)

        # 2) 菜单与权限点入库（loaddata 幂等 upsert）
        call_command("loaddata", *[str(path) for path in seed_paths], verbosity=0)
        self.stdout.write(f"--bootstrap: 菜单/权限点已入库（{ctx['app_label']}/seeds/）")

        # 3) 角色授权：显式 --grant-to 才执行（授权属业务决策，不自动猜）
        grant_to = [code.strip() for code in (options.get("grant_to") or "").split(",") if code.strip()]
        if not grant_to:
            self.stdout.write("--bootstrap: 未指定 --grant-to，跳过角色授权（菜单管理里手工勾选亦可）")
            return
        self._grant_roles(ctx, grant_to)

    @staticmethod
    def _seed_paths(ctx: dict[str, Any], options: dict[str, Any]) -> list[Path]:
        """本应用种子文件（menumeta 先于 menu：meta 被菜单 OneToOne 引用）。

        文件名以 ``manifest.json`` 的登记为准（兼容早期按模型拆分的 ``menu_<model>.json``），
        未登记时用规范名。
        """

        seed_dir = Path(options["output"] or settings.PROJECT_DIR) / ctx["app_label"] / "seeds"
        paths = [seed_dir / _seed_target_name(seed_dir, model, f"{stem}.json") for model, stem, _order in _SEED_FILES]
        found = [path for path in paths if path.is_file()]
        if not found:
            raise CommandError(f"未找到应用种子文件：{seed_dir}（先去掉 --skip-menu-seed 生成）")
        return found

    @staticmethod
    def _seed_relpath(ctx: dict[str, Any]) -> str:
        """生成产物在仓库内的相对路径（后续步骤提示用；未落盘时给规范名）。

        返回**两个文件的命令行参数**（menumeta 先于 menu）：菜单行引用 meta 主键，
        单装 ``menu.json`` 会因外键缺失失败。
        """

        seed_dir = Path(settings.PROJECT_DIR) / ctx["app_label"] / "seeds"
        existing: list[str] = []
        for model, stem, _order in _SEED_FILES:
            name = _seed_target_name(seed_dir, model, f"{stem}.json")
            if (seed_dir / name).is_file():
                existing.append(f"{ctx['app_label']}/seeds/{name}")
        if existing:
            return " ".join(existing)
        return f"{ctx['app_label']}/seeds/menumeta.json {ctx['app_label']}/seeds/menu.json"

    @staticmethod
    def _backfill_model_links(seed_paths: list[Path], model_pk: Any) -> None:
        """回填生成期未解析到的 model 关联（生成先行、同步在后的时序）。

        已解析的种子本就带 pk，此处不产生写入（幂等）。
        """

        for seed_path in seed_paths:
            entries = json.loads(seed_path.read_text(encoding="utf-8"))
            patched = False
            for entry in entries:
                if (
                    entry["model"] == "system.menu"
                    and entry["fields"].get("menu_type") == 2
                    and not entry["fields"].get("model")
                ):
                    entry["fields"]["model"] = [str(model_pk)]
                    patched = True
            if patched:
                seed_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _grant_roles(self, ctx: dict[str, Any], grant_to: list[str]) -> None:
        permissions = list(PERMISSION_ACTIONS)
        if ctx.get("with_import_export"):
            permissions.extend(IMPORT_EXPORT_PERMISSIONS)
        menu_names = [ctx["component"]] + [f"{action}:{ctx['component']}" for action, _, _ in permissions]
        menus = list(Menu.objects.filter(name__in=menu_names, deleted_at__isnull=True))
        roles = list(UserRole.objects.filter(code__in=grant_to, is_active=True, deleted_at__isnull=True))
        missing_roles = sorted(set(grant_to) - {role.code for role in roles})
        if missing_roles:
            self.stdout.write(self.style.WARNING(f"--bootstrap: 角色不存在或已删除，跳过：{missing_roles}"))
        if menus and roles:
            for role in roles:
                role.menu.add(*menus)
            self.stdout.write(f"--bootstrap: 已授予角色 {[role.code for role in roles]} 共 {len(menus)} 个菜单/权限点")


def _seed_target_name(seed_dir: Path, model: str, default: str) -> str:
    """本模型在种子清单里登记的文件名；未登记（或清单损坏）时用规范名。"""

    manifest_path = seed_dir / "manifest.json"
    if not manifest_path.is_file():
        return default
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    entries = manifest.get("files")
    if not isinstance(entries, list):
        return default
    for entry in entries:
        if isinstance(entry, dict) and entry.get("model") == model:
            name = entry.get("name")
            if isinstance(name, str) and name:
                return name
    return default
