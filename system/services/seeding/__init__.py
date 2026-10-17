#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""内置种子的发现、规划、校验与装载准备。

对外只经本模块导入；具体实现按职责分文件：

- :mod:`paths` —— 种子路径的唯一入口
- :mod:`manifest` —— ``seeds/manifest.json`` 的解析与校验
- :mod:`discover` —— 平台层 + 各 app 层的发现
- :mod:`plan` —— 应用选择、依赖闭包、拓扑排序
- :mod:`validate` —— 结构 / 主键 / 模型校验
- :mod:`loader` —— 装配与装载准备
- :mod:`rows` —— 读文件、模块裁剪、冲突预检、落盘
- :mod:`report` —— 命令行输出
"""

from .discover import Discovery as Discovery
from .discover import discover as discover
from .grants import GrantDeclaration as GrantDeclaration
from .grants import GrantReport as GrantReport
from .grants import apply_grants as apply_grants
from .grants import load_grants as load_grants
from .grants import merge_grants as merge_grants
from .grants import role_menu_names as role_menu_names
from .grants import seed_role_menu_names as seed_role_menu_names
from .grants import validate_grants as validate_grants
from .legacy import LEGACY_MODEL_LABELS as LEGACY_MODEL_LABELS
from .legacy import legacy_models as legacy_models
from .loader import model_classes as model_classes
from .loader import prepare as prepare
from .manifest import parse_manifest as parse_manifest
from .manifest import platform_manifest_path as platform_manifest_path
from .ownership import OwnershipSummary as OwnershipSummary
from .ownership import analyse as analyse_ownership
from .ownership import menu_owners as menu_owners
from .ownership import row_owners as row_owners
from .paths import app_seed_dir as app_seed_dir
from .paths import app_seed_file as app_seed_file
from .paths import manifest_file as manifest_file
from .paths import platform_dir as platform_dir
from .paths import platform_file as platform_file
from .paths import seed_file as seed_file
from .plan import build_plan as build_plan
from .report import count_rows as count_rows
from .report import format_ownership as format_ownership
from .report import format_plan as format_plan
from .report import format_problems as format_problems
from .report import has_error as has_error
from .rows import build_fixtures as build_fixtures
from .rows import read_model_rows as read_model_rows
from .rows import read_seed_rows as read_seed_rows
from .rows import read_specs as read_specs
from .types import GRANTS_MODEL as GRANTS_MODEL
from .types import KIND_CONTRACT as KIND_CONTRACT
from .types import KIND_GENERATED as KIND_GENERATED
from .types import KIND_SAMPLE as KIND_SAMPLE
from .types import KINDS as KINDS
from .types import LEVEL_ERROR as LEVEL_ERROR
from .types import LEVEL_WARNING as LEVEL_WARNING
from .types import PLATFORM as PLATFORM
from .types import LoadReport as LoadReport
from .types import Problem as Problem
from .types import SeedFileSpec as SeedFileSpec
from .types import SeedManifest as SeedManifest
from .types import SeedPlan as SeedPlan
from .types import SeedPlanError as SeedPlanError
from .validate import ValidationResult as ValidationResult
from .validate import collect_problems as collect_problems
from .validate import validate_plan as validate_plan

__all__ = [
    "Discovery",
    "GRANTS_MODEL",
    "GrantDeclaration",
    "GrantReport",
    "KINDS",
    "KIND_CONTRACT",
    "KIND_GENERATED",
    "KIND_SAMPLE",
    "LEGACY_MODEL_LABELS",
    "LEVEL_ERROR",
    "LEVEL_WARNING",
    "LoadReport",
    "OwnershipSummary",
    "PLATFORM",
    "Problem",
    "SeedFileSpec",
    "SeedManifest",
    "SeedPlan",
    "SeedPlanError",
    "ValidationResult",
    "analyse_ownership",
    "app_seed_dir",
    "app_seed_file",
    "apply_grants",
    "build_fixtures",
    "build_plan",
    "collect_problems",
    "count_rows",
    "discover",
    "format_ownership",
    "format_plan",
    "format_problems",
    "has_error",
    "legacy_models",
    "load_grants",
    "manifest_file",
    "menu_owners",
    "merge_grants",
    "model_classes",
    "row_owners",
    "parse_manifest",
    "platform_dir",
    "platform_file",
    "platform_manifest_path",
    "prepare",
    "read_model_rows",
    "read_seed_rows",
    "read_specs",
    "role_menu_names",
    "seed_file",
    "seed_role_menu_names",
    "validate_grants",
    "validate_plan",
]
