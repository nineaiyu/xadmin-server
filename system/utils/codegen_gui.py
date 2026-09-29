#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器 GUI 适配层：复用 ``generate_crud`` 引擎（CLI 为唯一实现），只做三件事。

1. **模型清单**：枚举可生成 CRUD 的一级业务模型（仓库内 app、非抽象、非自动生成）；
2. **字段计划**：选中模型后回显引擎推导的字段映射（GUI「字段编辑」在此基础上
   做 include/exclude 收敛，不另立第二套推导规则）；
3. **产物预览 / 打包下载**：与 CLI 同一条 ``_build_context → _collect_artifacts``
   管线，**不落盘**（``_emit`` 不参与）——产物按仓库前缀（xadmin-server /
   xadmin-client）标注相对路径，前端预览树与 zip 内路径同源。

安全口径：端点只读（模型内省 + 模板渲染 + zip 下载），不写仓库、不写库；
访问面由菜单种子权限点收口（默认仅超管，授予即用）。
"""

import io
import zipfile
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings

from common.management.commands._generate_crud import Command
from common.utils import get_logger

logger = get_logger(__name__)

# zip 内的仓库前缀（两仓布局与本地开发目录一致）
SERVER_REPO_PREFIX = "xadmin-server"
CLIENT_REPO_PREFIX = "xadmin-client"
# 引擎渲染前端产物路径时的占位根：GUI 不落盘，客户端仓库不存在也可生成
_CLIENT_ROOT_SENTINEL = "/.xadmin-codegen-client"


class CodegenError(Exception):
    """GUI 生成请求不合法（模型不可生成 / 字段选择越界 / 参数缺失）。"""


def _engine() -> Command:
    return Command()


def _base_options(payload: dict) -> dict:
    """CLI options dict 的 GUI 形态：布尔开关 + 命名参数，字段选择单独处理。"""
    return {
        "component": str(payload.get("component") or ""),
        "url_prefix": str(payload.get("url_prefix") or ""),
        "frontend_dir": str(payload.get("frontend_dir") or ""),
        "parent": str(payload.get("parent") or ""),
        "with_import_export": bool(payload.get("with_import_export")),
        "with_tags": bool(payload.get("with_tags")),
        "with_tests": False,  # 测试骨架属仓库代码，GUI 下载场景不生成（CLI 可用）
        "with_module": bool(payload.get("with_module")),
        "register_app": False,
        "module_id": str(payload.get("module_id") or ""),
        "module_level": payload.get("module_level") or "optional",
        "output": "",  # 默认项目根：仅用于推导路径，GUI 不写盘
        "skip_frontend": False,
        "skip_menu_seed": False,
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
    fields = []
    for name in ctx["serializer_fields"]:
        if name == "pk":
            fields.append({"name": "pk", "verbose_name": "ID", "type": "pk", "in_table": True, "required": False})
            continue
        field = next((item for item in model._meta.fields + model._meta.many_to_many if item.name == name), None)
        if field is None:
            fields.append({"name": name, "verbose_name": name, "type": "unknown", "in_table": False, "required": False})
            continue
        fields.append(
            {
                "name": name,
                "verbose_name": str(getattr(field, "verbose_name", name) or name),
                "type": type(field).__name__,
                "in_table": name in ctx["table_fields"],
                "required": bool(getattr(field, "required", False) if field.is_relation else not field.blank),
            }
        )
    return {
        "label": model._meta.label,
        "verbose_name": str(model._meta.verbose_name),
        "defaults": {
            "component": ctx["component"],
            "url_prefix": ctx["url_prefix"],
            "frontend_dir": ctx["frontend_dir"],
        },
        "fields": fields,
    }


def _apply_field_selection(ctx: dict, include: list[str], exclude: list[str]) -> None:
    """GUI 字段编辑：include 白名单 / exclude 黑名单收敛引擎推导的字段面。

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


def _relative_artifacts(artifacts: list[dict]) -> list[dict]:
    """产物路径转「仓库前缀 + 相对路径」；无路径的 notice 产物原样保留。"""
    server_root = Path(settings.PROJECT_DIR).resolve()
    client_root = Path(_CLIENT_ROOT_SENTINEL)
    rows = []
    for artifact in artifacts:
        path = artifact.get("path")
        row = {key: artifact[key] for key in ("label", "content", "mode", "key") if key in artifact}
        if artifact.get("notice"):
            row["notice"] = artifact["notice"]
        if path is None:
            row["path"] = ""
        else:
            path = Path(path)
            try:
                relative = path.relative_to(server_root)
                repo = SERVER_REPO_PREFIX
            except ValueError:
                relative = path.relative_to(client_root)
                repo = CLIENT_REPO_PREFIX
            row["path"] = f"{repo}/{relative.as_posix()}"
        rows.append(row)
    return rows


def build_artifacts(payload: dict) -> list[dict]:
    """按 GUI 表单构建产物清单（与 CLI 同一管线，不落盘）。"""
    label = str(payload.get("model") or "")
    if not label:
        raise CodegenError("缺少 model 参数")
    engine = _engine()
    model = _model_or_raise(label)
    options = _base_options(payload)
    ctx = engine._build_context(model, options)
    _apply_field_selection(ctx, list(payload.get("include_fields") or []), list(payload.get("exclude_fields") or []))
    artifacts = engine._collect_artifacts(ctx, options)
    return _relative_artifacts(artifacts)


def build_zip(payload: dict) -> bytes:
    """产物打包 zip（路径即仓库相对路径；模板代码无需加密）。"""
    return io_bytes_zip(build_artifacts(payload))


def io_bytes_zip(artifacts: list[dict]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for artifact in artifacts:
            if not artifact.get("path") or artifact.get("mode") == "notice":
                continue
            archive.writestr(artifact["path"], artifact["content"])
    return buffer.getvalue()
