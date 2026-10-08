# -*- coding: utf-8 -*-
"""内核 settings 契约清单（宿主对接面）。

框架内核（``common``）读取的每一个 Django settings 键都在本模块登记。宿主
（xadmin-server 或二开项目）按本清单提供配置：缺省列语义为「必给」的键必须提供
（代码内为直读，缺失即读取方报错），其余键缺失即按 ``default`` 值工作。

条目字段（``KernelSetting``；键名即清单字典的 key）::

    kind          期望类型（文档 / 评审用，不作为运行期校验）
    purpose       一句话用途
    default       解析后的缺省值（``REQUIRED`` = 代码内为直读，宿主必须提供）
    default_expr  代码内缺省表达式（守护测试与源码锁步；``None`` = 代码内为直读）
    consumers     读取该键的内核模块（相对 ``common`` 包路径，守护测试锁步）
    framework     True = Django 内置键（``django.conf.global_settings`` 恒有值）

读取约定（新增读取必须同步登记）::

    # 有默认值：一律 getattr 形式
    value = getattr(settings, "MODULE_PRESET", "full")
    # 直读键
    value = settings.SECRET_KEY
    # 新代码推荐走本模块的读取助手（默认值语义与契约面同源）
    value = kernel_setting("MODULE_PRESET")          # 缺省回落契约默认值
    value = kernel_required_setting("SECRET_KEY")    # 缺失报 ImproperlyConfigured

守护：``tests/unit/common/test_settings_contract.py`` 静态扫描内核源码做双向
校验（源码读取 ⊆ 契约面、契约面 ⊆ 源码读取、缺省表达式与消费方清单锁步）；
文档表见 ``docs/architecture/kernel-package.md`` §三（同由守护测试对照）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class _Required:
    """``REQUIRED`` 哨兵：代码内为直读（无默认值），宿主必须提供。"""

    _instance: _Required | None = None

    def __new__(cls) -> _Required:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "REQUIRED"

    def __bool__(self) -> bool:  # 防 `if entry.default:` 误判
        raise TypeError("REQUIRED 是哨兵，不支持真值判断；请显式比较 entry.default is REQUIRED")


REQUIRED = _Required()


@dataclass(frozen=True)
class KernelSetting:
    """单个内核 settings 键的契约条目（键名即清单字典的 key，不重复存储）。"""

    kind: str
    purpose: str
    default: Any = REQUIRED
    default_expr: str | None = None
    consumers: tuple[str, ...] = field(default_factory=tuple)
    framework: bool = False


# 清单表内用短别名：单键单行，键级 diff 一目了然（行长不受 E501 约束）
KS = KernelSetting

KERNEL_SETTINGS: dict[str, KernelSetting] = {
    # ---------------------------------------------------------------
    # 一、身份与加密材料
    # ---------------------------------------------------------------
    "SECRET_KEY": KS(
        "str",
        "Django 密钥；内核用于字段级加密派生材料与签名（base/utils.py::signer、fields/char.py）",
        consumers=("base/utils.py", "fields/char.py"),
        framework=True,
    ),
    "FIELD_ENCRYPTION_KEY": KS(
        "str", "字段级加密主密钥（AESCipherV3 根材料；空 = 回落到 SECRET_KEY 派生并告警）", consumers=("base/utils.py",)
    ),
    "FIELD_ENCRYPTION_LEGACY_KEYS": KS(
        "list[str]", "字段级加密历史密钥（轮换期解密旧密文）", consumers=("base/utils.py",)
    ),
    "SECURITY_AES_V1_DECRYPT_ENABLED": KS(
        "bool",
        "是否允许解密 AES v1（Salted__）旧格式请求体；关闭即 fail-closed",
        default=True,
        default_expr="True",
        consumers=("base/utils.py",),
    ),
    "AUTH_USER_MODEL": KS(
        "str", "用户模型标签（内核模型基类/权限组件解析用户模型用）", consumers=("core/models.py",), framework=True
    ),
    "ALLOWED_HOSTS": KS(
        "list[str]",
        "允许主机（CSP 上报的站内域名判定；Django 安全基建）",
        default=None,
        default_expr="None",
        consumers=("api/csp.py", "swagger/views.py"),
        framework=True,
    ),
    "SECURITY_LOGIN_LIMIT_TIME": KS(
        "int", "登录锁定提示中的锁定时长（分钟，文案展示用）", consumers=("swagger/views.py",)
    ),
    # ---------------------------------------------------------------
    # 二、验证码
    # ---------------------------------------------------------------
    "VERIFY_CODE_TTL": KS("int", "验证码有效期（秒）", consumers=("utils/verify_code.py",)),
    "VERIFY_CODE_LIMIT": KS("int", "验证码单目标发送次数上限（限流窗口内）", consumers=("utils/verify_code.py",)),
    "VERIFY_CODE_LENGTH": KS("int", "验证码位数", consumers=("utils/verify_code.py",)),
    "VERIFY_CODE_UPPER_CASE": KS("bool", "验证码字符集是否含大写字母", consumers=("utils/verify_code.py",)),
    "VERIFY_CODE_LOWER_CASE": KS("bool", "验证码字符集是否含小写字母", consumers=("utils/verify_code.py",)),
    "VERIFY_CODE_DIGIT_CASE": KS("bool", "验证码字符集是否含数字", consumers=("utils/verify_code.py",)),
    # ---------------------------------------------------------------
    # 三、三层权限（接口 / 数据 / 字段）
    # ---------------------------------------------------------------
    "PERMISSION_WHITE_URL": KS(
        "list[str]",
        "免鉴权 URL 白名单（method+path 正则；含登录/验证码/文档等公开端点）",
        consumers=("core/permission.py", "core/utils.py"),
    ),
    "PERMISSION_SHOW_PREFIX": KS(
        "list[str]", "需要展示按钮级权限码的 URL 前缀正则（路由收集时附带 auths）", consumers=("core/utils.py",)
    ),
    "PERMISSION_DATA_ENABLED": KS(
        "bool", "数据权限总开关（关闭即所有查询不做行级裁剪）", consumers=("core/filter.py",)
    ),
    "PERMISSION_DATA_AUTH_APPS": KS(
        "list[str]", "纳入数据权限编译的 app 白名单（其余 app 不注册模型维度）", consumers=("core/utils.py",)
    ),
    "PERMISSION_FIELD_ENABLED": KS(
        "bool",
        "字段权限总开关（序列化时按角色×菜单裁剪字段）",
        consumers=("core/controlled_lookup.py", "core/fields_related.py", "core/permission.py", "core/serializers.py"),
    ),
    # ---------------------------------------------------------------
    # 四、路径、存储与上传
    # ---------------------------------------------------------------
    "BASE_DIR": KS(
        "str",
        "工程根目录（进程管理命令定位项目/静态资源用）",
        consumers=("management/commands/services/hands.py",),
        framework=True,
    ),
    "PROJECT_DIR": KS("str", "工程根目录（启动自检读取版本/配置）", consumers=("startup.py",)),
    "DATA_DIR": KS(
        "str",
        "运行期数据目录（IP 归属地库等运行期文件）",
        consumers=("management/commands/services/hands.py", "utils/ip/geoip/utils.py", "utils/ip/ipip/utils.py"),
    ),
    "MEDIA_ROOT": KS(
        "str",
        "上传文件根目录（本地存储后端与文件链路）",
        consumers=("storage/backend.py", "storage/utils.py"),
        framework=True,
    ),
    "MEDIA_URL": KS("str", "媒体 URL 前缀（存储后端拼接访问地址）", consumers=("storage/backend.py",), framework=True),
    "MEDIA_X_ACCEL_PREFIX": KS(
        "str",
        "nginx X-Accel-Redirect 内网前缀（空 = 由 Django 直接回文件流）",
        default="",
        default_expr="''",
        consumers=("utils/media.py",),
    ),
    "FILE_UPLOAD_SIZE": KS("int", "单文件上传大小上限（字节）", consumers=("core/modelset/upload.py",)),
    "EXPORT_MAX_LIMIT": KS("int", "同步导出最大行数（超过引导异步导出）", consumers=("drf/renders/base.py",)),
    # ---------------------------------------------------------------
    # 五、缓存与令牌
    # ---------------------------------------------------------------
    "CACHE_KEY_TEMPLATE": KS(
        "dict[str, str]",
        "缓存键模板表（token 黑名单 / 待办状态等跨组件共享键名）",
        consumers=("cache/channel.py", "cache/storage.py"),
    ),
    "SIMPLE_JWT": KS(
        "dict[str, Any]",
        "simplejwt 配置（内核按 ACCESS/REFRESH 生命周期管理黑名单缓存 TTL）",
        consumers=("cache/storage.py",),
    ),
    "REST_FRAMEWORK": KS(
        "dict[str, Any]", "DRF 配置（内核读取认证/渲染等全局项）", consumers=("drf/renders/base.py", "utils/request.py")
    ),
    # ---------------------------------------------------------------
    # 六、异步任务（celery）
    # ---------------------------------------------------------------
    "CELERY_BROKER_URL": KS(
        "str", "celery broker 地址（指标端点脱敏展示用）", default="", default_expr="''", consumers=("metrics.py",)
    ),
    "CELERY_TASK_ALWAYS_EAGER": KS(
        "bool",
        "任务是否同步执行（异步导出/导入在 eager 环境直接落结果）",
        default=False,
        default_expr="False",
        consumers=("core/modelset/import_export/export_actions.py", "core/modelset/import_export/import_actions.py"),
    ),
    "CELERY_LOG_DIR": KS("str", "任务日志目录（任务日志文件落盘根）", consumers=("celery/utils.py",)),
    "CELERY_HEAVY_POOL": KS(
        "str",
        "重任务队列执行池类型（进程管理命令拉起 worker 用）",
        consumers=("management/commands/services/services/celery_heavy.py",),
    ),
    "CELERY_HEAVY_CONCURRENCY": KS(
        "int",
        "重任务队列并发度（进程管理命令拉起 worker 用）",
        consumers=("management/commands/services/services/celery_heavy.py",),
    ),
    "CELERY_FLOWER_HOST": KS("str", "Flower 监听地址（进程管理命令拉起 Flower 用）", consumers=("celery/flower.py",)),
    "CELERY_FLOWER_PORT": KS("int", "Flower 监听端口", consumers=("celery/flower.py",)),
    "CELERY_FLOWER_AUTH": KS("str", "Flower Basic Auth（user:pass，空 = 不启用）", consumers=("celery/flower.py",)),
    # ---------------------------------------------------------------
    # 七、通知与监控
    # ---------------------------------------------------------------
    "EMAIL_HOST_USER": KS("str", "发件账号（系统邮件通知发件人兜底）", consumers=("tasks.py",), framework=True),
    "EMAIL_FROM": KS("str", "发件显示名（系统邮件通知；空 = 回落 EMAIL_HOST_USER）", consumers=("tasks.py",)),
    "EMAIL_SUBJECT_PREFIX": KS("str", "系统邮件主题前缀", consumers=("tasks.py",), framework=True),
    "SECURITY_MONITOR_CPU_PERCENT_MAX": KS("float", "CPU 使用率告警阈值（%）", consumers=("notifications.py",)),
    "SECURITY_MONITOR_MEMORY_USED_MAX": KS("float", "内存使用率告警阈值（%）", consumers=("notifications.py",)),
    "SECURITY_MONITOR_DISK_USED_MAX": KS("float", "磁盘使用率告警阈值（%）", consumers=("notifications.py",)),
    "SECURITY_MONITOR_CPU_LOAD_MAX": KS("float", "CPU 负载告警阈值（load1）", consumers=("notifications.py",)),
    "METRICS_ENABLED": KS(
        "bool",
        "Prometheus 指标端点开关（关闭即 404 / no-op）",
        default=False,
        default_expr="False",
        consumers=("api/metrics.py",),
    ),
    "METRICS_TOKEN": KS(
        "str", "指标端点令牌（空 = 端点拒访）", default="", default_expr="''", consumers=("api/metrics.py",)
    ),
    "HEALTH_CHECK_SKIP_CELERY": KS(
        "bool",
        "健康检查是否跳过 celery 探针（测试/离线环境用）",
        default=False,
        default_expr="False",
        consumers=("utils/health.py",),
    ),
    # ---------------------------------------------------------------
    # 八、模块裁剪
    # ---------------------------------------------------------------
    "MODULE_PRESET": KS(
        "str",
        "模块发行预设（core / standard / full；代码内默认常量 DEFAULT_PRESET = full）",
        default="full",
        default_expr="DEFAULT_PRESET",
        consumers=("core/modules/registry.py",),
    ),
    "MODULE_ENABLE": KS(
        "list[str] | tuple[str, ...]",
        "显式启用模块清单（预设之外的加白）",
        default=(),
        default_expr="()",
        consumers=("core/modules/registry.py",),
    ),
    "MODULE_DISABLE": KS(
        "list[str] | tuple[str, ...]",
        "显式停用模块清单（预设之内的裁剪）",
        default=(),
        default_expr="()",
        consumers=("core/modules/registry.py",),
    ),
    # ---------------------------------------------------------------
    # 九、路由与工程装配
    # ---------------------------------------------------------------
    "ROOT_URLCONF": KS("str", "根 URLConf（路由收集/OpenAPI 生成用）", consumers=("core/utils.py",)),
    "ROUTE_IGNORE_URL": KS("list[str]", "路由收集忽略表（正则，不纳入权限点/路由树）", consumers=("core/utils.py",)),
    "XADMIN_APPS": KS("list[str]", "二开 app 注册表（路由/WS/周期任务自动发现）", consumers=("core/utils.py",)),
    "DB_PREFIX": KS("str", "物理表前缀（多环境共库时的表空间隔离）", consumers=("core/db/prefix.py",)),
    # ---------------------------------------------------------------
    # 十、运行模式与操作日志
    # ---------------------------------------------------------------
    "DEBUG": KS(
        "bool",
        "调试模式（媒体文件直出、开发态分支）",
        default=False,
        default_expr="False",
        consumers=("base/decorators.py", "management/commands/services/services/flower.py", "utils/media.py"),
        framework=True,
    ),
    "DEBUG_DEV": KS(
        "bool",
        "开发态增强开关（额外诊断输出/演示能力）",
        default=False,
        default_expr="False",
        consumers=("base/decorators.py", "core/exception.py", "signal_handlers.py"),
    ),
    "API_LOG_ENABLE": KS(
        "bool",
        "操作日志开关（中间件写 OperationLog）",
        default=None,
        default_expr="None",
        consumers=("core/middleware.py",),
    ),
    "API_LOG_METHODS": KS(
        "set[str] | list[str]",
        "纳入操作日志的 HTTP 方法",
        default=None,
        default_expr="None",
        consumers=("core/middleware.py",),
    ),
    "API_LOG_IGNORE": KS(
        "dict[str, list[str]]",
        "操作日志忽略表（model label / path → 方法清单）",
        default=None,
        default_expr="None",
        consumers=("core/middleware.py",),
    ),
    "API_MODEL_MAP": KS(
        "dict[str, str]",
        "操作日志模块名映射（path → 展示名，缺省回落模型 label）",
        consumers=("core/middleware.py",),
        framework=True,
    ),
    "ATOMIC_REQUESTS_SKIP_READ_ACTIONS": KS(
        "bool",
        "纯读请求（GET/HEAD）免 ATOMIC_REQUESTS 事务",
        default=True,
        default_expr="True",
        consumers=("core/atomic_read.py",),
    ),
    "RECYCLE_BIN_RETENTION_DAYS": KS(
        "int",
        "回收站保留天数（清理任务与回收站列表倒计时）",
        default=30,
        default_expr="30",
        consumers=("core/modelset/recycle.py", "tasks.py"),
    ),
    # ---------------------------------------------------------------
    # 十一、本地化与协议
    # ---------------------------------------------------------------
    "LANGUAGE_CODE": KS(
        "str",
        "语言码（IP 归属地本地化、时间格式）",
        consumers=("utils/ip/geoip/utils.py", "utils/ip/utils.py"),
        framework=True,
    ),
    "DEFAULT_CHARSET": KS(
        "str", "默认字符集（axios 表单解析回退编码）", consumers=("drf/parsers/axios_form_data.py",), framework=True
    ),
}


def render_default(entry: KernelSetting) -> str:
    """契约默认值的文档渲染（文档表与守护测试同源，防两处口径漂移）。"""
    if entry.default is REQUIRED:
        return "必给"
    return repr(entry.default)


def kernel_setting(name: str) -> Any:
    """按契约读取内核 settings 键（缺失回落契约默认值；``REQUIRED`` 键缺失即报错）。"""
    from django.conf import settings

    entry = KERNEL_SETTINGS.get(name)
    if entry is None:
        raise KeyError(f"{name} 未登记在 common/settings_contract.py（新增读取必须同步登记契约）")
    if entry.default is REQUIRED:
        return kernel_required_setting(name)
    return getattr(settings, name, entry.default)


def kernel_required_setting(name: str) -> Any:
    """按契约直读内核 settings 键（未登记或缺失均报 ImproperlyConfigured）。"""
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    entry = KERNEL_SETTINGS.get(name)
    if entry is None:
        raise KeyError(f"{name} 未登记在 common/settings_contract.py（新增读取必须同步登记契约）")
    try:
        return getattr(settings, name)
    except AttributeError as exc:
        raise ImproperlyConfigured(
            f"settings.{name} 缺失：内核 settings 契约标记为必给键（用途：{entry.purpose}）"
        ) from exc
