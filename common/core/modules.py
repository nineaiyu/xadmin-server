#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : modules
# author : ly_13
# date : 2026/09/17
"""功能模块注册表（可裁剪架构）。

本文件是「哪些功能存在、哪些默认开启」的唯一事实源（单一数据源）：

- 每个模块声明：等级 / 依赖 / 菜单子树 / 请求路由前缀 / 补充权限路径；
- 装配开关来自 config.yml 的 ``MODULE_PRESET`` / ``MODULE_ENABLE`` / ``MODULE_DISABLE``；
- 所有裁剪动作都从声明派生，不允许在别处再写一份模块清单。

生效范围（软裁剪）：
    1. 请求路由：命中禁用模块前缀的 HTTP 请求直接 404（``ModuleGateMiddleware``）；
    2. 菜单与权限点：禁用模块的菜单子树与权限码从用户路由/鉴权结果中隐藏；
    3. 周期任务：禁用模块声明的周期任务不再注册，历史注册条目一并清理。

裁剪语义（红线）：
    1. 关闭模块只隐藏与拦截，**不删除任何业务数据**；重新开启即恢复；
    2. ``core`` 等级模块不可关闭（装配、鉴权与菜单结构依赖它们）；
    3. 依赖未满足时启动期 fail-fast，不做隐式连带禁用；
    4. 默认 ``preset=full``（全部开启），与改造前行为零差异。

已知边界（P1）：WebSocket 通道不拦截（页面与 REST 均已不可达）；前端构建产物
仍包含全部页面（按需构建裁剪见后续批次）。边界与后续计划见
``docs/adr/ADR-045-modular-trimmable-architecture.md``。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import import_module

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q

from common.utils import get_logger

logger = get_logger(__name__)

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


# ---------------------------------------------------------------------------
# 模块清单（唯一事实源）
# ---------------------------------------------------------------------------
MODULES: tuple[ModuleSpec, ...] = (
    # ------------------------- 内核（不可裁剪） -------------------------
    ModuleSpec("core_auth", "身份与访问（登录/注册/重置/会话/MFA/第三方登录）", CORE),
    ModuleSpec("core_rbac", "组织与权限（用户/角色/菜单/部门/字典/数据与字段权限）", CORE),
    ModuleSpec("core_config", "系统配置（站点/安全/消息/水印/个人配置）", CORE),
    ModuleSpec("core_file", "文件与流转（附件/预览/导入/导出下载中心）", CORE),
    ModuleSpec("core_notify", "站内通知中心", CORE),
    ModuleSpec("core_log", "审计与在线（操作日志/登录日志/在线用户）", CORE),
    ModuleSpec("core_open_credential", "个人访问令牌（PAT，个人凭证）", CORE),
    # ------------------------- 标配（默认开启，可裁剪） -------------------------
    ModuleSpec(
        "ops",
        "运维监控（主机监控/定时任务/Flower）",
        STANDARD,
        menus=("SystemMonitor", "celery"),
        routes=(r"^/api/system/monitor", r"^/api/system/tasks/", r"^/api/flower/"),
        note="关闭后主机心跳采集与资源告警周期任务一并停止",
    ),
    ModuleSpec(
        "approval",
        "敏感操作审批（拦截 + 审批单）",
        STANDARD,
        menus=("SystemApprovalRequest",),
        permissions=("api/system/approvals",),
        routes=(r"^/api/system/approvals",),
        note="关闭后 APPROVAL_REQUIRED_PATHS 拦截整体失效（无审批单可落）",
    ),
    ModuleSpec(
        "datamask",
        "数据脱敏",
        STANDARD,
        menus=("SystemDataMaskRule",),
        routes=(r"^/api/system/mask-rules",),
    ),
    ModuleSpec(
        "ldap",
        "目录同步（LDAP/AD）",
        STANDARD,
        menus=("SettingLdap",),
        routes=(r"^/api/settings/ldap",),
        note="模块控制页面与接口可达；是否真正启用目录同步由 LDAP_AUTH_ENABLED 决定",
    ),
    # ------------------------- 可选（按需开启） -------------------------
    ModuleSpec(
        "chat",
        "聊天室",
        OPTIONAL,
        menus=("Chat",),
        routes=(r"^/api/chat/",),
        note="WebSocket 通道不随模块拦截（REST 与页面已不可达）",
    ),
    ModuleSpec(
        "ai",
        "AI 助手与知识库",
        OPTIONAL,
        menus=("AiAssistant", "AiAssistantConfig", "AiKnowledge"),
        routes=(r"^/api/system/ai/",),
        note="聊天室内的 AI 助手属于 chat 模块，不受本开关影响",
    ),
    ModuleSpec(
        "analysis",
        "数据分析（数据集/仪表盘/报表/大屏）",
        OPTIONAL,
        menus=("DataDashboard", "DataDataset", "DataReport", "DataScreen"),
        routes=(
            r"^/api/system/datasets",
            r"^/api/system/dashboards",
            r"^/api/system/screens",
            r"^/api/system/reports",
        ),
        note="关闭后定时报表周期任务一并停止",
    ),
    ModuleSpec(
        "dform",
        "表单采集（设计器 + 我的填报）",
        OPTIONAL,
        menus=("FormDesigner", "FormMySubmission"),
        routes=(r"^/api/system/dynamic-forms", r"^/api/system/dynamic-form-submissions"),
    ),
    ModuleSpec(
        "approval_flow",
        "审批流引擎（流程定义/实例/委托/请假）",
        OPTIONAL,
        menus=(
            "SystemApprovalFlow",
            "SystemApprovalInstance",
            "SystemApprovalDelegation",
            "SystemLeave",
        ),
        routes=(
            r"^/api/system/approval-flows",
            r"^/api/system/approval-instances",
            r"^/api/system/approval-delegations",
            r"^/api/system/leaves",
        ),
    ),
    ModuleSpec(
        "webhook",
        "事件订阅（出站 Webhook）",
        OPTIONAL,
        menus=("WebhookSubscription", "WebhookDelivery"),
        routes=(r"^/api/system/webhooks/",),
    ),
    ModuleSpec(
        "open_platform",
        "开放平台（API 应用 + OAuth 授权码）",
        OPTIONAL,
        menus=("IntegrationApiApp",),
        routes=(r"^/api/system/api-applications", r"^/api/system/open/"),
        note="个人访问令牌（PAT）属内核，不随本模块关闭",
    ),
    ModuleSpec(
        "search",
        "全局搜索",
        OPTIONAL,
        menus=("SearchData",),
        permissions=("api/system/global-search",),
        routes=(r"^/api/system/global-search", r"^/api/system/search/"),
    ),
    ModuleSpec("scim", "SCIM 2.0 目录同步", OPTIONAL, routes=(r"^/api/scim/v2/",)),
)

# 内置模块索引（仅内置声明；运行期取数一律用 module_index()，它包含 app 侧声明）
_MODULE_INDEX = {spec.id: spec for spec in MODULES}


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


def _as_tuple(value) -> tuple:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [item for item in re.split(r"[,\s]+", value) if item]
    return tuple(str(item).strip() for item in value if str(item).strip())


def _configured_preset() -> str:
    preset = str(getattr(settings, "MODULE_PRESET", DEFAULT_PRESET) or DEFAULT_PRESET).strip().lower()
    if preset not in PRESETS:
        raise ImproperlyConfigured(f"MODULE_PRESET={preset!r} 无效，可选值：{', '.join(PRESETS)}")
    return preset


def resolve_modules() -> ModuleResolution:
    """解析生效模块集合（带缓存；配置变更需重启进程）。"""

    return _resolve_modules_cached()


@lru_cache(maxsize=1)
def discovered_modules() -> tuple:
    """各已安装 app 通过 ``{app}/modules.py`` 声明的模块（第三方功能模块扩展点）。

    与 ``XADMIN_APPS`` → ``{app}/config.py``（路由注册）对称的二开契约：
    app 侧提供模块级 ``MODULES`` 元组（``ModuleSpec``），随 app 安装自动纳入清单，
    无需改动本项目源码。导入失败只告警跳过（扩展点故障不应拖垮内核启动）。
    """

    from django.apps import apps as django_apps

    discovered = []
    for app_config in django_apps.get_app_configs():
        module_path = f"{app_config.name}.modules"
        try:
            module = import_module(module_path)
            declared = tuple(getattr(module, "MODULES", ()) or ())
        except ModuleNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001 扩展声明异常不影响内核启动
            logger.warning("load module declaration failed: %s (%s)", module_path, exc)
            continue
        if not declared:
            continue
        logger.info("module declaration found: %s (%s 个)", module_path, len(declared))
        discovered.extend(declared)
    return tuple(discovered)


@lru_cache(maxsize=1)
def all_module_specs() -> tuple:
    """内置模块 + 第三方 app 声明的模块（模块清单的唯一取数口）。"""

    return MODULES + discovered_modules()


@lru_cache(maxsize=1)
def module_index() -> dict:
    """模块 id → 声明（含第三方）；id 重复直接 fail-fast。"""

    index: dict = {}
    for spec in all_module_specs():
        if spec.id in index:
            raise ImproperlyConfigured(f"模块 id 重复：{spec.id}（内置模块与 app 声明冲突）")
        index[spec.id] = spec
    return index


@lru_cache(maxsize=1)
def _resolve_modules_cached() -> ModuleResolution:
    return _resolve(
        _configured_preset(),
        _as_tuple(getattr(settings, "MODULE_ENABLE", ())),
        _as_tuple(getattr(settings, "MODULE_DISABLE", ())),
    )


def preview_modules(preset=None, enable=None, disable=None) -> ModuleResolution:
    """按给定组合解析模块（不改动运行期配置，供 CLI 预演与文档生成）。

    未传的项沿用当前配置；组合非法时与真实配置一样 fail-fast。
    """

    preset_value = _configured_preset() if preset is None else str(preset).strip().lower()
    if preset_value not in PRESETS:
        raise ImproperlyConfigured(f"MODULE_PRESET={preset_value!r} 无效，可选值：{', '.join(PRESETS)}")
    enable_value = _as_tuple(getattr(settings, "MODULE_ENABLE", ())) if enable is None else _as_tuple(enable)
    disable_value = _as_tuple(getattr(settings, "MODULE_DISABLE", ())) if disable is None else _as_tuple(disable)
    return _resolve(preset_value, enable_value, disable_value)


def _resolve(preset: str, enable: tuple, disable: tuple) -> ModuleResolution:
    index = module_index()
    unknown = [mid for mid in (*enable, *disable) if mid not in index]
    if unknown:
        raise ImproperlyConfigured(
            f"MODULE_ENABLE/MODULE_DISABLE 中存在未知模块：{', '.join(unknown)}；可用模块：{', '.join(sorted(index))}"
        )

    allowed_levels = _PRESET_LEVELS[preset]
    enabled = {spec.id for spec in all_module_specs() if spec.level in allowed_levels}
    conflict = sorted(set(disable) & {spec.id for spec in all_module_specs() if spec.level == CORE})
    if conflict:
        raise ImproperlyConfigured(f"内核模块不可关闭：{', '.join(conflict)}")
    enabled.update(enable)
    enabled.difference_update(disable)

    missing = sorted(f"{mid}→{dep}" for mid in enabled for dep in index[mid].depends if dep not in enabled)
    if missing:
        raise ImproperlyConfigured("模块依赖未满足（请同时开启被依赖模块，或关闭依赖方）：" + "；".join(missing))

    disabled = frozenset(index) - frozenset(enabled)
    resolution = ModuleResolution(
        preset=preset,
        enabled=frozenset(enabled),
        disabled=disabled,
        overrides=tuple(f"{mid}={'+' if mid in enable else '-'}" for mid in (*enable, *disable)),
    )
    logger.info(
        "module resolution: preset=%s enabled=%s disabled=%s",
        preset,
        len(resolution.enabled),
        ",".join(sorted(disabled)) or "-",
    )
    return resolution


def reset_module_state() -> None:
    """清空模块解析与派生缓存（配置变更/测试隔离用）。"""

    # 用 globals() 反射清理：测试会把 all_module_specs 换成普通函数，
    # 直接 .cache_clear() 会 AttributeError
    names = (
        "_resolve_modules_cached",
        "_disabled_specs",
        "_disabled_route_regexes",
        "_disabled_menu_pks_uncached",
        "all_module_specs",
        "module_index",
        "discovered_modules",
    )
    for name in names:
        cache_clear = getattr(globals().get(name), "cache_clear", None)
        if cache_clear:
            cache_clear()


@lru_cache(maxsize=1)
def _disabled_specs() -> tuple:
    resolution = resolve_modules()
    index = module_index()
    return tuple(index[mid] for mid in sorted(resolution.disabled))


def enabled_module_ids() -> frozenset:
    return resolve_modules().enabled


def disabled_module_ids() -> frozenset:
    return resolve_modules().disabled


def is_module_enabled(module_id) -> bool:
    """模块是否启用；``None`` 视为内核（未声明模块归属的功能一律按启用处理）。"""

    if not module_id:
        return True
    return str(module_id) in resolve_modules().enabled


def module_signature() -> str:
    """当前模块组合的短签名（缓存键/诊断用）。"""

    resolution = resolve_modules()
    if resolution.is_full:
        return resolution.preset
    digest = hashlib.sha1(",".join(sorted(resolution.enabled)).encode("utf-8")).hexdigest()[:10]
    return f"{resolution.preset}-{digest}"


@lru_cache(maxsize=1)
def _disabled_route_regexes() -> tuple:
    patterns = []
    for spec in _disabled_specs():
        for prefix in spec.routes:
            patterns.append(re.compile(prefix))
    return tuple(patterns)


def disabled_route_patterns() -> tuple:
    """禁用模块的请求路径正则（空元组 = 无裁剪，调用方走零开销旁路）。"""

    return _disabled_route_regexes()


def permission_prefixes_of(specs) -> tuple:
    """给定模块集合的权限点 path 前缀。

    两条来源合并：
    1. ``ModuleSpec.permissions``：菜单树覆盖不到的权限点（如全局搜索）；
    2. ``ModuleSpec.routes``：路由前缀去掉 ``^`` 与前导 ``/`` 后的形态
       （权限点 path 形如 ``api/chat/room$``），保证权限码隐藏不依赖菜单层级
       ——管理员把权限点挂到别的菜单下也不会漏。
    """

    prefixes = []
    for spec in specs:
        prefixes.extend(spec.permissions)
        for route in spec.routes:
            path = route.lstrip("^").lstrip("/")
            if path:
                prefixes.append(path)
    return tuple(prefixes)


def disabled_permission_prefixes() -> tuple:
    """当前停用模块的权限点 path 前缀。"""

    return permission_prefixes_of(_disabled_specs())


@lru_cache(maxsize=1)
def _disabled_menu_pks_uncached() -> frozenset:
    if not _disabled_specs():
        return frozenset()
    try:
        from system.services import Menu

        rows = list(Menu.objects.values_list("pk", "parent_id", "menu_type", "name", "path"))
    except Exception as exc:  # noqa: BLE001 迁移期/空库等场景下裁剪退化为不裁剪
        logger.warning("module menu filter skipped: %s", exc)
        return frozenset()

    hidden = compute_hidden_menu_pks(rows)
    if hidden:
        logger.info("module menu filter: %s menus hidden", len(hidden))
    return hidden


def _disabled_menu_pks() -> frozenset:
    return _disabled_menu_pks_uncached()


def compute_hidden_menu_pks(rows, names=None, prefixes=None) -> frozenset:
    """计算需隐藏的菜单主键（纯函数：运行期过滤与种子导入共用同一口径）。

    规则：

    1. ``names`` 指定的菜单根 → 其整棵子树（含权限点行）；
    2. 权限点行 ``path`` 命中 ``prefixes`` → 该行本身（不依赖菜单层级，防止被改挂）；
    3. 子节点被全部隐藏的目录 → 目录本身（避免前端出现空分组）。

    :param rows: 可迭代的 ``(pk, parent_id, menu_type, name, path)``
    :param names: 需隐藏的菜单根 name（默认取停用模块声明）
    :param prefixes: 需隐藏的权限点 path 前缀（默认取停用模块声明）
    """

    names = {name for spec in _disabled_specs() for name in spec.menus} if names is None else set(names)
    prefixes = disabled_permission_prefixes() if prefixes is None else tuple(prefixes)
    if not names and not prefixes:
        return frozenset()

    # 入参可能是生成器（种子侧直接传推导式），必须物化：rows 会被遍历两次
    rows = list(rows)
    children: dict = {}
    by_name: dict = {}
    hidden: set = set()
    # _prefix_regex 返回的是给 path__regex 用的模式串，这里需要编译后再自行匹配
    permission_re = re.compile(_prefix_regex(prefixes)) if prefixes else None
    for pk, parent_id, menu_type, name, path in rows:
        children.setdefault(parent_id, []).append(pk)
        by_name.setdefault(name, []).append(pk)
        if permission_re is not None and menu_type == MENU_TYPE_PERMISSION and path and permission_re.match(path):
            hidden.add(pk)

    hidden.update(pk for name in names for pk in by_name.get(name, ()))
    stack = list(hidden)
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in hidden:
                hidden.add(child)
                stack.append(child)

    directories = [(pk, parent_id) for pk, parent_id, menu_type, _n, _p in rows if menu_type == MENU_TYPE_DIRECTORY]
    changed = True
    while changed:
        changed = False
        for pk, _parent_id in directories:
            if pk in hidden:
                continue
            kids = children.get(pk) or []
            if kids and all(child in hidden for child in kids):
                hidden.add(pk)
                changed = True
    return frozenset(hidden)


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


def filter_menu_queryset(queryset):
    """剔除禁用模块的菜单行（含权限点行）；无禁用模块时原样返回。"""

    resolution = resolve_modules()
    if resolution.is_full:
        return queryset
    hidden = _disabled_menu_pks()
    prefixes = disabled_permission_prefixes()
    if not hidden and not prefixes:
        return queryset
    try:
        from system.services import Menu

        # 显式逐条 OR（不使用空 Q 起步：空 Q 参与 OR 的语义在 SQL 层不稳妥）
        conditions = []
        if hidden:
            conditions.append(Q(pk__in=tuple(hidden)))
        if prefixes:
            conditions.append(Q(menu_type=Menu.MenuChoices.PERMISSION, path__regex=_prefix_regex(prefixes)))
        condition = conditions[0]
        for extra in conditions[1:]:
            condition |= extra
        return queryset.exclude(condition)
    except Exception as exc:  # noqa: BLE001 依赖异常时不隐藏（仅记录），避免影响主链路
        logger.warning("module menu filter skipped: %s", exc)
        return queryset


@lru_cache(maxsize=8)
def _prefix_regex(prefixes: tuple) -> str:
    return "^(" + "|".join(re.escape(prefix) for prefix in prefixes) + ")"


def invalidate_trimmed_caches() -> int:
    """清理受模块裁剪影响的缓存（进程启动时调用一次）。

    菜单路由与用户权限码缓存 TTL 均为 24 小时，而模块组合只在 config.yml 变更
    并重启后生效：启动时清理一次，避免「模块已关停、菜单仍显示 24 小时」。
    未配置停用模块（preset=full 且无覆盖）时直接返回 0，无任何开销。
    """

    if resolve_modules().is_full:
        return 0
    from django.core.cache import cache

    removed = 0
    for pattern in (
        "magic_cache_data_get_user_permission*",
        "magic_cache_response_UserRoutesAPIView*",
    ):
        try:
            removed += cache.delete_pattern(pattern) or 0
        except Exception as exc:  # noqa: BLE001 缓存异常不影响启动
            logger.warning("module trim invalidate %s failed: %s", pattern, exc)
    logger.info("module trim: invalidated %s cached entries", removed)
    return removed


def preset_module_ids(preset: str) -> frozenset:
    """某预设下默认启用的模块集合（用于计算「相对预设的覆盖项」）。"""

    if preset not in PRESETS:
        raise ImproperlyConfigured(f"MODULE_PRESET={preset!r} 无效，可选值：{', '.join(PRESETS)}")
    levels = _PRESET_LEVELS[preset]
    return frozenset(spec.id for spec in all_module_specs() if spec.level in levels)


def config_snippet(resolution: ModuleResolution | None = None) -> str:
    """生成可直接粘贴到 config.yml 的裁剪配置片段（CLI 与模块清单接口共用）。"""

    resolution = resolution or resolve_modules()
    preset_ids = preset_module_ids(resolution.preset)
    lines = [f"MODULE_PRESET: {resolution.preset}"]
    enabled_overrides = sorted(set(resolution.enabled) - preset_ids)
    disabled_overrides = sorted(set(resolution.disabled) & preset_ids)
    if enabled_overrides:
        lines.append("MODULE_ENABLE:")
        lines.extend(f"  - {mid}" for mid in enabled_overrides)
    if disabled_overrides:
        lines.append("MODULE_DISABLE:")
        lines.extend(f"  - {mid}" for mid in disabled_overrides)
    return "\n".join(lines)


def modules_report(resolution: ModuleResolution | None = None) -> list:
    """模块清单报表（CLI / 文档生成共用）。"""

    resolution = resolution or resolve_modules()
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "level": spec.level,
            "depends": list(spec.depends),
            "menus": len(spec.menus),
            "routes": len(spec.routes),
            "enabled": spec.id in resolution.enabled,
            "note": spec.note,
        }
        for spec in all_module_specs()
    ]
