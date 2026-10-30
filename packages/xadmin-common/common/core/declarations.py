#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""app 侧声明契约：权限同步例外的归属、发现与合并。

与 ``{app}/modules.py``（裁剪声明）、``{app}/ai_declarations.py``（AI 动作声明）
对称的二开契约：app 在自己的 ``declarations.py`` 里用 ``AppDeclarations`` 声明
权限同步所需的例外清单（扫描跳过 / 审计豁免 / 父菜单归属 / 运行期自建权限点等），
平台同步内核（``system.services.permission_sync``）经 ``discover_app_declarations``
发现并按 ``merge_app_declarations`` 合并——例外数据跟着所属 app 走，内核不再
硬编码任何 app 内部约定（固定 pk 段、专属路由前缀等）。

约定：

- ``declarations.py`` 必须是纯数据（只 import 本模块）：发现期在任何 app 的
  命令路径上执行，import 模型 / 视图会放大启动与循环依赖风险；
- 键与前缀在多个 app 间重复视为配置冲突，合并时直接 fail-fast；
- ``app`` 字段可省略，发现期自动回填 app label（同名声明移动 app 时少改一处）。
"""

from dataclasses import dataclass, field, replace
from importlib import import_module
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from common.utils import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AppDeclarations:
    """一个 app 对权限同步内核的例外与归属声明（全部可选，按需填写）。

    各字段的语义与消费点见 ``system/services/permission_sync``（扫描 / 审计 /
    绑定校正 / 种子回写）；本结构只承载纯数据，不含任何行为。
    """

    app: str = ""
    #: 不参与权限点扫描的路由前缀（该 app 的端点不生成权限点）
    skip_route_prefixes: tuple[str, ...] = ()
    #: 审计豁免前缀：有权限点但不在可扫描路由面内（代理注册 / 页签级权限位）
    audit_skip_prefixes: tuple[str, ...] = ()
    #: 已知「同端点双权限码」：(路由正则原文, 方法)，各自服务不同 UI 入口
    audit_known_duplicates: tuple[tuple[str, str], ...] = ()
    #: 单权限码覆盖同端点多方法：路由正则原文（含 ``$``）→ 方法集合
    shared_method_paths: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: 无同源权限点时按路由前缀指定父菜单：前缀 → Menu.name 或 (权限码后缀, 父菜单 name)
    parent_menu_map: dict[str, Any] = field(default_factory=dict)
    #: custom 权限面（自管权限类）视图的显式豁免：视图路径 → 自管原因
    permission_exempt_views: dict[str, str] = field(default_factory=dict)
    #: 运行期自建权限点的 pk 前缀：绑定 / 回写 / 审计由 app 自身命令维护
    runtime_permission_pk_prefixes: tuple[str, ...] = ()


def discover_app_declarations() -> tuple[AppDeclarations, ...]:
    """各已安装 app 经 ``{app}/declarations.py`` 声明的例外（未声明返回空元组）。

    与 ``modules.py`` 扩展点同一故障语义：导入失败只告警跳过（app 侧声明问题
    不应拖垮平台同步内核），声明的 ``app`` 字段缺省时回填 app label。
    """

    from django.apps import apps as django_apps

    discovered: list[AppDeclarations] = []
    for app_config in django_apps.get_app_configs():
        module_path = f"{app_config.label}.declarations"
        try:
            module = import_module(module_path)
        except ModuleNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001 扩展点故障不影响内核
            logger.warning("load app declarations failed: %s (%s)", module_path, exc)
            continue
        declared = getattr(module, "DECLARATIONS", None)
        if declared is None:
            continue
        if not isinstance(declared, AppDeclarations):
            logger.warning("app declarations ignored (DECLARATIONS 不是 AppDeclarations): %s", module_path)
            continue
        if not declared.app:
            declared = replace(declared, app=app_config.label)
        discovered.append(declared)
    return tuple(discovered)


def _merge_sequence(target: list[Any], items: Any, app: str, name: str) -> None:
    for item in items:
        if item in target:
            raise ImproperlyConfigured(f"app 声明冲突：{name}={item!r} 被多个 app 重复声明（{app}）")
        target.append(item)


def _merge_mapping(target: dict[Any, Any], items: Any, app: str, name: str) -> None:
    for key, value in items.items():
        if key in target:
            raise ImproperlyConfigured(f"app 声明冲突：{name}[{key!r}] 被多个 app 重复声明（{app}）")
        target[key] = value


def merge_app_declarations(declarations: tuple[AppDeclarations, ...]) -> AppDeclarations:
    """合并各 app 声明为一个聚合视图（序列保持 app 声明顺序，键冲突 fail-fast）。"""

    skip: list[str] = []
    audit_skip: list[str] = []
    duplicates: list[tuple[str, str]] = []
    runtime: list[str] = []
    shared: dict[str, tuple[str, ...]] = {}
    parents: dict[str, Any] = {}
    exempt: dict[str, str] = {}
    for declared in declarations:
        app = declared.app or "<未命名>"
        _merge_sequence(skip, declared.skip_route_prefixes, app, "skip_route_prefixes")
        _merge_sequence(audit_skip, declared.audit_skip_prefixes, app, "audit_skip_prefixes")
        _merge_sequence(duplicates, declared.audit_known_duplicates, app, "audit_known_duplicates")
        _merge_sequence(runtime, declared.runtime_permission_pk_prefixes, app, "runtime_permission_pk_prefixes")
        _merge_mapping(shared, declared.shared_method_paths, app, "shared_method_paths")
        _merge_mapping(parents, declared.parent_menu_map, app, "parent_menu_map")
        _merge_mapping(exempt, declared.permission_exempt_views, app, "permission_exempt_views")
    return AppDeclarations(
        skip_route_prefixes=tuple(skip),
        audit_skip_prefixes=tuple(audit_skip),
        audit_known_duplicates=tuple(duplicates),
        shared_method_paths=shared,
        parent_menu_map=parents,
        permission_exempt_views=exempt,
        runtime_permission_pk_prefixes=tuple(runtime),
    )
