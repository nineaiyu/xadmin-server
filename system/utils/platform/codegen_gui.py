#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器 GUI 适配层：复用 ``generate_crud`` 引擎（CLI 为唯一实现），只做四件事。

1. **模型清单**：枚举可生成 CRUD 的一级业务模型（仓库内 app、非抽象、非自动生成）；
2. **字段计划**：选中模型后回显引擎推导的字段映射（逐字段默认面 + 可绑定字典类型），
   GUI「字段编辑」在此之上做覆盖收敛（显示名 / 必填 / 只读 / input_type / 字典绑定 /
   表格列 / 搜索 / 排序，见 :mod:`system.utils.platform.codegen_fields`）；
3. **产物预览 / 打包下载**：与 CLI 同一条 ``_build_context → _collect_artifacts``
   管线，**不落盘**（``_emit`` 不参与）——产物按仓库前缀（xadmin-server /
   xadmin-client）标注相对路径，前端预览树与 zip 内路径同源；zip 附 NEXT_STEPS.md
   （后续步骤与 CLI 打印同口径）；批量多模型一次打包（共享表单选项 + 逐模型引擎
   默认字段计划，冲突文件保留首个并附合并提示）；
4. **测试骨架**：``with_tests`` 同 CLI（GUI 下载 zip 场景同样可携带 pytest 骨架）。

安全口径：端点只读（模型内省 + 字典查询 + 模板渲染 + zip 下载），不写仓库、不写库；
访问面由菜单种子权限点收口（默认仅超管，授予即用）。
"""

import io
import zipfile
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management.base import CommandError

from common.utils import get_logger
from devtools.management.commands._generate_crud import Command
from system.utils.platform.codegen_fields import CodegenError, apply_field_overrides, list_dict_types, plan_fields

logger = get_logger(__name__)

# zip 内的仓库前缀（两仓布局与本地开发目录一致）
SERVER_REPO_PREFIX = "xadmin-server"
CLIENT_REPO_PREFIX = "xadmin-client"
# 引擎渲染前端产物路径时的占位根：GUI 不落盘，客户端仓库不存在也可生成
_CLIENT_ROOT_SENTINEL = "/.xadmin-codegen-client"
# 模块等级合法面（与 CLI --module-level choices 对齐）
_MODULE_LEVELS = ("core", "standard", "optional")


def _engine() -> Command:
    return Command()


def _base_options(payload: dict) -> dict:
    """CLI options dict 的 GUI 形态：布尔开关 + 命名参数，字段选择单独处理。"""
    module_level = payload.get("module_level")
    return {
        "component": str(payload.get("component") or ""),
        "url_prefix": str(payload.get("url_prefix") or ""),
        "frontend_dir": str(payload.get("frontend_dir") or ""),
        "parent": str(payload.get("menu_parent") or payload.get("parent") or ""),
        "menu_title": str(payload.get("menu_title") or ""),
        "menu_icon": str(payload.get("menu_icon") or ""),
        "ordering": str(payload.get("ordering") or ""),
        "with_import_export": bool(payload.get("with_import_export")),
        "with_tags": bool(payload.get("with_tags")),
        "with_tests": bool(payload.get("with_tests")),
        "with_module": bool(payload.get("with_module")),
        "register_app": False,
        "module_id": str(payload.get("module_id") or ""),
        "module_level": module_level if module_level in _MODULE_LEVELS else "optional",
        "output": "",  # 默认项目根：仅用于推导路径，GUI 不写盘
        "skip_frontend": not bool(payload.get("with_frontend", True)),
        "skip_menu_seed": bool(payload.get("skip_menu_seed")),
        "skip_ai": not bool(payload.get("with_ai", True)),
        "frontend_root": _CLIENT_ROOT_SENTINEL,
        "bootstrap": False,
        "grant_to": "",
        "force": False,
        "dry_run": True,
    }


def list_generatable_models() -> list[dict]:
    """可生成模型清单：仓库内一级 app 的普通模型（抽象 / 自动生成 / 交换模型除外）。

    app 目录必须是 PROJECT_ROOT 的一级子目录（仓库 app 布局约定，与跨 app 门禁的
    discover_apps 同口径）——venv 内的三方包（.venv 在项目目录内）天然排除。
    """
    project_dir = Path(settings.PROJECT_DIR).resolve()
    rows = []
    for app_config in django_apps.get_app_configs():
        app_path = Path(app_config.path).resolve()
        if app_path.parent != project_dir:
            continue  # 非仓库一级 app（django.contrib / venv 内三方包）不在生成面
        for model in app_config.get_models():
            if model._meta.abstract or model._meta.auto_created or model._meta.proxy:
                continue
            rows.append(
                {
                    "label": model._meta.label,
                    "app_label": app_config.label,
                    "verbose_name": str(model._meta.verbose_name),
                    "table": model._meta.db_table,
                    "field_count": len(model._meta.fields),
                }
            )
    rows.sort(key=lambda item: (item["app_label"], item["label"]))
    return rows


def _model_or_raise(label: str):
    engine = _engine()
    try:
        return engine._resolve_model(label)
    except Exception as exc:  # noqa: BLE001 CommandError 文案直接透出
        raise CodegenError(str(exc)) from exc


def model_plan(label: str) -> dict:
    """选中模型的字段计划 + 命名默认值（GUI 表单的初始值）。"""
    engine = _engine()
    model = _model_or_raise(label)
    ctx = engine._build_context(model, _base_options({}))
    return {
        "label": model._meta.label,
        "verbose_name": str(model._meta.verbose_name),
        "defaults": {
            "component": ctx["component"],
            "url_prefix": ctx["url_prefix"],
            "frontend_dir": ctx["frontend_dir"],
        },
        "fields": plan_fields(ctx, model),
        "dict_types": list_dict_types(),
    }


def _apply_field_selection(ctx: dict, include: list[str], exclude: list[str]) -> None:
    """字段选择（旧口径）：include 白名单 / exclude 黑名单收敛引擎推导的字段面。

    pk 恒保留（主键不可排除）；include 优先于 exclude；表格列 / extra_kwargs /
    过滤字段随序列化器字段面同步收敛（生成物内部一致性由同一 ctx 保证）。
    """
    unknown = []
    known = {item.name for item in ctx["model"]._meta.fields} | {item.name for item in ctx["model"]._meta.many_to_many}
    known.add("pk")
    for name in [*include, *exclude]:
        if name not in known:
            unknown.append(name)
    if unknown:
        raise CodegenError(f"未知字段：{', '.join(sorted(set(unknown)))}")

    if include:
        allowed = {"pk", *include}
        ctx["serializer_fields"] = [name for name in ctx["serializer_fields"] if name in allowed]
    elif exclude:
        banned = set(exclude) - {"pk"}
        ctx["serializer_fields"] = [name for name in ctx["serializer_fields"] if name not in banned]
    kept = set(ctx["serializer_fields"])
    ctx["table_fields"] = [name for name in ctx["table_fields"] if name in kept]
    ctx["extra_kwargs"] = {name: kwargs for name, kwargs in ctx["extra_kwargs"].items() if name in kept}
    ctx["filter_custom_fields"] = [name for name in ctx["filter_custom_fields"] if name in kept]
    ctx["filter_meta_fields"] = [name for name in ctx["filter_meta_fields"] if name in kept]


def _prepare(engine: Command, payload: dict) -> tuple[dict, list[dict], list[str]]:
    """单模型准备：解析参数 → ctx → 字段收敛 → 产物与后续步骤（不落盘）。"""
    label = str(payload.get("model") or "")
    if not label:
        raise CodegenError("缺少 model 参数")
    model = _model_or_raise(label)
    options = _base_options(payload)
    try:
        ctx = engine._build_context(model, options)
    except CommandError as exc:  # noqa: BLE001 参数校验失败（如非法 --ordering）透出为 1001
        raise CodegenError(str(exc)) from exc
    _apply_field_selection(ctx, list(payload.get("include_fields") or []), list(payload.get("exclude_fields") or []))
    apply_field_overrides(ctx, list(payload.get("fields") or []))
    artifacts = engine._collect_artifacts(ctx, options)
    return ctx, artifacts, engine._next_steps(ctx, options)


def _next_steps_markdown(sections: list[tuple[str, list[str]]]) -> str:
    """后续步骤 → NEXT_STEPS.md（与 CLI 尾部打印同口径，多模型分节）。"""
    lines = ["# 代码生成后续步骤", "", "以下命令在项目根执行：", ""]
    for title, steps in sections:
        lines.extend([f"## {title}", ""])
        lines.extend(f"{index}. {step}" for index, step in enumerate(steps, start=1))
        lines.append("")
    lines.extend(
        [
            "## 复核",
            "",
            "- 关联字段 input_type 是否符合数据量（大数据量换 api-search-* 形态）；",
            "- 菜单上级是否要挂到已有目录（--parent 或菜单管理里调整）。",
            "",
        ]
    )
    return "\n".join(lines)


def _next_steps_artifact(content: str, suffix: str = "") -> dict:
    return {
        "label": "后续步骤",
        "path": Path("NEXT_STEPS.md"),
        "content": content,
        "mode": "readme",
        "key": f"next-steps{suffix}",
    }


def _dedupe_artifacts(artifacts: list[dict]) -> list[dict]:
    """批量打包去重：同路径同内容保留首个；内容冲突保留首个并附合并提示。"""
    kept: list[dict] = []
    seen: dict[str, str] = {}
    conflicts: list[str] = []
    for artifact in artifacts:
        if artifact.get("mode") == "notice" or artifact.get("path") is None:
            kept.append(artifact)
            continue
        key = Path(artifact["path"]).as_posix()
        if key not in seen:
            seen[key] = artifact["content"]
            kept.append(artifact)
        elif seen[key] != artifact["content"]:
            conflicts.append(key)
    for index, key in enumerate(sorted(set(conflicts))):
        kept.append(
            {
                "label": "合并提示",
                "path": None,
                "content": "",
                "mode": "notice",
                "key": f"conflict-{index}",
                "notice": f"{key} 多模型生成内容不一致（如各自的路由注册行），zip 保留首个模型版本，"
                "其余模型的注册行请参照该文件手工合并",
            }
        )
    return kept


def _relative_path(path: Path) -> str:
    """产物路径转展示路径：仓库前缀 + 相对路径；zip 根级文件（NEXT_STEPS.md）原样。"""
    server_root = Path(settings.PROJECT_DIR).resolve()
    client_root = Path(_CLIENT_ROOT_SENTINEL)
    for root, prefix in ((server_root, SERVER_REPO_PREFIX), (client_root, CLIENT_REPO_PREFIX)):
        try:
            return f"{prefix}/{path.relative_to(root).as_posix()}"
        except ValueError:
            continue
    return path.as_posix()


def _relative_artifacts(artifacts: list[dict]) -> list[dict]:
    """产物路径转「仓库前缀 + 相对路径」；无路径的 notice 产物原样保留。"""
    rows = []
    for artifact in artifacts:
        path = artifact.get("path")
        row = {key: artifact[key] for key in ("label", "content", "mode", "key") if key in artifact}
        if artifact.get("notice"):
            row["notice"] = artifact["notice"]
        row["path"] = "" if path is None else _relative_path(Path(path))
        rows.append(row)
    return rows


def build_artifacts(payload: dict) -> list[dict]:
    """按 GUI 表单构建产物清单（与 CLI 同一管线，不落盘，含 NEXT_STEPS.md）。"""
    engine = _engine()
    ctx, artifacts, steps = _prepare(engine, payload)
    title = f"{ctx['app_label']}.{ctx['model_name']}（{ctx['verbose_name']}）"
    artifacts.append(_next_steps_artifact(_next_steps_markdown([(title, steps)])))
    return _relative_artifacts(artifacts)


def build_zip(payload: dict) -> bytes:
    """产物打包 zip（路径即仓库相对路径；模板代码无需加密）。

    payload 带 ``models``（多模型清单）时走批量：共享表单选项 + 逐模型引擎默认
    字段计划（忽略字段级覆盖），冲突文件去重保留首个，NEXT_STEPS.md 合并为单文档。
    """
    models = payload.get("models")
    if not (isinstance(models, list) and models):
        return io_bytes_zip(build_artifacts(payload))

    engine = _engine()
    all_artifacts: list[dict] = []
    sections: list[tuple[str, list[str]]] = []
    for raw in models:
        single = {**payload, "model": str(raw)}
        single.pop("models", None)
        ctx, artifacts, steps = _prepare(engine, single)
        all_artifacts.extend(artifacts)
        sections.append((f"{ctx['app_label']}.{ctx['model_name']}（{ctx['verbose_name']}）", steps))
    all_artifacts = _dedupe_artifacts(all_artifacts)
    all_artifacts.append(_next_steps_artifact(_next_steps_markdown(sections), suffix="-batch"))
    return io_bytes_zip(_relative_artifacts(all_artifacts))


def io_bytes_zip(artifacts: list[dict]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for artifact in artifacts:
            if not artifact.get("path") or artifact.get("mode") == "notice":
                continue
            archive.writestr(artifact["path"], artifact["content"])
    return buffer.getvalue()
