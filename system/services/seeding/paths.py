#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""种子路径的唯一入口。

平台层固定为 ``<PROJECT_DIR>/loadjson/``（保留原名与「可直接 loaddata」的既有契约），
应用层固定为 ``<app_config.path>/seeds/``。除本模块外，代码里不应再出现
``... / "loadjson"`` 这类路径拼接——路径口径一旦分散，拆分后必然出现漏改。
"""

from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings

from .types import PLATFORM

PLATFORM_DIR_NAME = "loadjson"
APP_SEED_DIR_NAME = "seeds"
MANIFEST_NAME = "manifest.json"


def platform_dir(root: Path | str | None = None) -> Path:
    """平台共享层目录（``root`` 供导出工具写到自定义输出目录）。"""

    base = Path(root) if root is not None else Path(settings.PROJECT_DIR)
    return base / PLATFORM_DIR_NAME


def platform_file(name: str, root: Path | str | None = None) -> Path:
    """平台层单个种子文件。"""

    return platform_dir(root) / name


def app_seed_dir(app_label: str) -> Path:
    """某个 app 的种子目录（不要求目录存在）。"""

    try:
        config = django_apps.get_app_config(app_label)
    except LookupError as exc:  # pragma: no cover - 由调用方保证 app 已注册
        raise ValueError(f"未注册的应用 {app_label!r}，无法定位其种子目录") from exc
    return Path(config.path) / APP_SEED_DIR_NAME


def app_seed_file(app_label: str, name: str) -> Path:
    """某个 app 的单个种子文件。"""

    return app_seed_dir(app_label) / name


def manifest_file(app_label: str) -> Path:
    """某个 app 的种子清单文件。"""

    return app_seed_file(app_label, MANIFEST_NAME)


def seed_file(app: str, name: str) -> Path:
    """按层取种子文件：平台层走 ``loadjson/``，其余走 ``<app>/seeds/``。"""

    if app == PLATFORM:
        return platform_file(name)
    return app_seed_file(app, name)
