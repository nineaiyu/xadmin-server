#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""功能模块注册表：模块清单（唯一事实源）与装配解析。"""

import hashlib
import re
import sys
from functools import lru_cache
from importlib import import_module

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from common.utils import get_logger

from .override import load_override
from .specs import _PRESET_LEVELS, CORE, DEFAULT_PRESET, OPTIONAL, PRESETS, STANDARD, ModuleResolution, ModuleSpec

logger = get_logger(__name__)

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
        ws_routes=(r"^/ws/system/monitor/",),
        note="关闭后主机心跳采集与资源告警周期任务一并停止",
    ),
    ModuleSpec(
        "approval",
        "敏感操作审批（拦截 + 审批单 + 多级审批规则）",
        STANDARD,
        menus=("SystemApprovalRequest", "SystemApprovalRule"),
        permissions=("api/system/approvals", "api/system/approval-rules"),
        routes=(r"^/api/system/approvals", r"^/api/system/approval-rules"),
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
        ws_routes=(r"^/ws/chat/",),
        note="关闭后 REST、页面与 ws/chat 通道同步拦截",
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
        ws_routes=(r"^/ws/screen/",),
        note="关闭后定时报表周期任务与 ws/screen 展示通道一并停止",
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
    """解析生效模块集合（带缓存；配置变更需重启进程）。

    首次解析时顺带完成受裁剪影响的缓存清理（见 ``_ensure_trim_cache_invalidated``）：
    解析被刻意推迟到首次实际使用（Django 不鼓励在 app 初始化期访问数据库），
    而清理必须发生在任何请求被处理之前——首次解析发生在中间件装配期，早于请求。
    """

    resolution = _resolve_modules_cached()
    _ensure_trim_cache_invalidated(resolution)
    return resolution


def validate_deployment_config() -> ModuleResolution:
    """启动期校验部署基线（config.yml / 环境变量）并返回其解析结果。

    只读 settings、**不读数据库**：后台覆盖的校验推迟到首次实际解析（同上），
    避免在 ``AppConfig.ready()`` 中建立指向「尚未创建的测试库」的连接。
    """

    return _resolve(*_baseline())


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
def _baseline() -> tuple:
    """部署基线（config.yml / 环境变量）的 ``(preset, enable, disable)``。"""

    return (
        _configured_preset(),
        _as_tuple(getattr(settings, "MODULE_ENABLE", ())),
        _as_tuple(getattr(settings, "MODULE_DISABLE", ())),
    )


def deployment_config() -> tuple:
    """部署基线 ``(preset, enable, disable)``（供管理页展示「偏离了哪份基线」）。"""

    return _baseline()


def _effective_config() -> tuple:
    """生效配置：后台覆盖行优先（整体替换部署基线），无行回退部署基线。"""

    override = load_override()
    if override is None:
        return _baseline()
    return override.preset, override.enable, override.disable


def override_active() -> bool:
    """当前是否存在后台覆盖行（不缓存：管理页保存后需立即反映）。"""

    return load_override() is not None


@lru_cache(maxsize=1)
def _resolve_modules_cached() -> ModuleResolution:
    return _resolve(*_effective_config())


# 进程内一次性标记：模块解析被推迟到首次使用时，缓存清理随之推迟（见 resolve_modules）
_trim_cache_invalidated = False


def _ensure_trim_cache_invalidated(resolution: ModuleResolution) -> None:
    """首次解析后清理一次菜单路由 / 权限码缓存（无停用模块时零开销）。"""

    global _trim_cache_invalidated
    if _trim_cache_invalidated:
        return
    _trim_cache_invalidated = True
    from .gate import invalidate_trimmed_caches

    invalidate_trimmed_caches(resolution)


def desired_modules() -> ModuleResolution:
    """待生效的模块组合（后台覆盖优先；**不缓存**，保存后立即可见）。

    与 ``resolve_modules()`` 的区别：后者是进程启动时解析的「当前生效」态，
    保存覆盖行不得改变它（否则等同于热更新）。
    """

    override = load_override()
    if override is None:
        return preview_modules()
    return preview_modules(preset=override.preset, enable=override.enable, disable=override.disable)


def module_diff(effective: ModuleResolution, desired: ModuleResolution) -> dict:
    """生效态与待生效态的差异（供管理页展示「待重启生效」）。"""

    return {
        "enable": sorted(desired.enabled - effective.enabled),
        "disable": sorted(effective.enabled - desired.enabled),
        "preset_changed": effective.preset != desired.preset,
    }


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
    # 直接 .cache_clear() 会 AttributeError。
    # 拆包后缓存分布在 registry 与 gate 两个模块，这里逐模块清理同名缓存。
    from . import gate

    names = (
        "_resolve_modules_cached",
        "_baseline",
        "_disabled_specs",
        "_disabled_route_regexes",
        "_disabled_ws_regexes",
        "_disabled_menu_pks_uncached",
        "all_module_specs",
        "module_index",
        "discovered_modules",
    )
    for module in (sys.modules[__name__], gate):
        for name in names:
            cache_clear = getattr(getattr(module, name, None), "cache_clear", None)
            if cache_clear:
                cache_clear()

    # 一次性清理标记同步复位：下一次解析重新按新的生效组合清理缓存
    global _trim_cache_invalidated
    _trim_cache_invalidated = False


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
