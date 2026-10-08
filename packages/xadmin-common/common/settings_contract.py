# -*- coding: utf-8 -*-
"""内核 settings 契约清单（宿主对接面）。

框架内核（``common``）读取的每一个 Django settings 键都在本模块登记，**并且内核源码
一律经本模块的读取助手访问**（``kernel_setting`` / ``kernel_required_setting``）——
契约面是内核读取 settings 的唯一事实源，由 ``tests/unit/common/test_settings_contract.py``
静态守护（未登记读取即红 / 登记腐化即红 / 访问器形态与缺省状态错配即红）。

宿主（xadmin-server 或二开项目）按本清单提供配置：

- **缺省列「必给」（``REQUIRED``）**：内核没有可靠的兜底语义，宿主必须提供；缺失时
  访问器抛 ``ImproperlyConfigured`` 并带上本清单登记的用途提示（fail-fast，不静默降级）。
- **缺省列为具体值**：宿主可省略；省略时内核按该值工作（**零配置可用**），显式配置即覆盖。

条目字段（``KernelSetting``；键名即清单字典的 key）::

    kind          期望类型（文档 / 评审用，不作为运行期校验）
    purpose       一句话用途
    default       解析后的缺省值（``REQUIRED`` = 宿主必给）
    consumers     读取该键的内核模块（相对 ``common`` 包路径，守护测试锁步）
    framework     True = Django 内置键（``django.conf.global_settings`` 恒有值）

读取约定（新增读取必须同步登记）::

    from common.settings_contract import kernel_required_setting, kernel_setting

    value = kernel_setting("MODULE_PRESET")          # 有缺省的键：缺失回落契约缺省值
    value = kernel_required_setting("SECRET_KEY")    # 必给键：缺失报 ImproperlyConfigured

**缺省值收敛策略**（评审口径，新增缺省须同时满足三条）：

1. **正确可用**：缺省值下内核行为正确——不允许出现「有默认值但默认值下静默产出错误
   结果」的键（例：三层权限的两个总开关没有安全缺省，故保持 ``REQUIRED``）；
2. **不静默削弱安全能力**：缺省值不得比「宿主什么都不配」关闭更多安全面——该场景宁可
   走 ``REQUIRED`` fail-fast（例：``PERMISSION_WHITE_URL`` 缺省空表是 fail-closed 的
   收紧方向，可给缺省；权限开关放松方向，不给缺省）；
3. **与宿主出厂口径一致**：缺省值对齐 xadmin-server 的出厂默认（``server/conf/defaults.py``
   / ``server/conf/settings_defaults.py``），避免同一能力两套口径。

**宿主最小对接面**：``REQUIRED`` 键中除去 Django 内置键（``framework=True``，常规工程
装配恒有值）后的部分，是本契约真正的必配清单，见
``docs/architecture/kernel-package.md`` §三「宿主最小对接面」（守护测试锁步）。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


class _Required:
    """``REQUIRED`` 哨兵：内核没有可靠缺省，宿主必须提供。"""

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

# 读取端「键不存在」探针：与 settings 里合法值（含 None）区分
_MISSING = object()


@dataclass(frozen=True)
class KernelSetting:
    """单个内核 settings 键的契约条目（键名即清单字典的 key，不重复存储）。"""

    kind: str
    purpose: str
    default: Any = REQUIRED
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
        "str",
        "字段级加密主密钥（AESCipherV3 根材料；空 = 回落到 SECRET_KEY 派生并告警）",
        default="",
        consumers=("base/utils.py",),
    ),
    "FIELD_ENCRYPTION_LEGACY_KEYS": KS(
        "list[str]",
        "字段级加密历史密钥（轮换期解密旧密文）",
        default=(),
        consumers=("base/utils.py",),
    ),
    "SECURITY_AES_V1_DECRYPT_ENABLED": KS(
        "bool",
        "是否允许解密 AES v1（Salted__）旧格式请求体；关闭即 fail-closed",
        default=True,
        consumers=("base/utils.py",),
    ),
    "AUTH_USER_MODEL": KS(
        "str", "用户模型标签（内核模型基类/权限组件解析用户模型用）", consumers=("core/models.py",), framework=True
    ),
    "ALLOWED_HOSTS": KS(
        "list[str]",
        "允许主机（CSP 上报的站内域名判定；Django 安全基建）",
        default=None,
        consumers=("api/csp.py", "swagger/views.py"),
        framework=True,
    ),
    "SECURITY_LOGIN_LIMIT_TIME": KS(
        "int",
        "登录锁定提示中的锁定时长（分钟，文案展示用）",
        default=30,
        consumers=("swagger/views.py",),
    ),
    # ---------------------------------------------------------------
    # 二、验证码
    # ---------------------------------------------------------------
    "VERIFY_CODE_TTL": KS("int", "验证码有效期（秒）", default=300, consumers=("utils/verify_code.py",)),
    "VERIFY_CODE_LIMIT": KS(
        "int", "验证码单目标发送次数上限（限流窗口内）", default=60, consumers=("utils/verify_code.py",)
    ),
    "VERIFY_CODE_LENGTH": KS("int", "验证码位数", default=6, consumers=("utils/verify_code.py",)),
    "VERIFY_CODE_UPPER_CASE": KS(
        "bool", "验证码字符集是否含大写字母", default=False, consumers=("utils/verify_code.py",)
    ),
    "VERIFY_CODE_LOWER_CASE": KS(
        "bool", "验证码字符集是否含小写字母", default=False, consumers=("utils/verify_code.py",)
    ),
    "VERIFY_CODE_DIGIT_CASE": KS("bool", "验证码字符集是否含数字", default=True, consumers=("utils/verify_code.py",)),
    # ---------------------------------------------------------------
    # 三、三层权限（接口 / 数据 / 字段）
    # ---------------------------------------------------------------
    "PERMISSION_WHITE_URL": KS(
        "dict[str, list[str]]",
        "免鉴权 URL 白名单（path 正则 → 方法清单；缺省空表 = 无免鉴权端点，fail-closed）",
        default={},
        consumers=("core/permission.py", "core/utils.py"),
    ),
    "PERMISSION_SHOW_PREFIX": KS(
        "list[str]",
        "需要展示按钮级权限码的 URL 前缀正则（路由收集时附带 auths；缺省空表 = 不附带）",
        default=[],
        consumers=("core/utils.py",),
    ),
    "PERMISSION_DATA_ENABLED": KS(
        "bool", "数据权限总开关（关闭即所有查询不做行级裁剪）", consumers=("core/filter.py",)
    ),
    "PERMISSION_DATA_AUTH_APPS": KS(
        "list[str]",
        "纳入数据权限编译的 app 白名单（其余 app 不注册模型维度；缺省空表 = 不注册）",
        default=[],
        consumers=("core/utils.py",),
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
        consumers=("utils/media.py",),
    ),
    "FILE_UPLOAD_SIZE": KS(
        "int",
        "单文件上传大小上限（字节）",
        default=10 * 1024 * 1024,
        consumers=("core/modelset/upload.py",),
    ),
    "EXPORT_MAX_LIMIT": KS(
        "int",
        "同步导出最大行数（超过引导异步导出）",
        default=20000,
        consumers=("drf/renders/base.py",),
    ),
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
    "CELERY_BROKER_URL": KS("str", "celery broker 地址（指标端点脱敏展示用）", default="", consumers=("metrics.py",)),
    "CELERY_TASK_ALWAYS_EAGER": KS(
        "bool",
        "任务是否同步执行（异步导出/导入在 eager 环境直接落结果）",
        default=False,
        consumers=("core/modelset/import_export/export_actions.py", "core/modelset/import_export/import_actions.py"),
    ),
    "CELERY_LOG_DIR": KS("str", "任务日志目录（任务日志文件落盘根）", consumers=("celery/utils.py",)),
    "CELERY_HEAVY_POOL": KS(
        "str",
        "重任务队列执行池类型（进程管理命令拉起 worker 用）",
        default="threads",
        consumers=("management/commands/services/services/celery_heavy.py",),
    ),
    "CELERY_HEAVY_CONCURRENCY": KS(
        "int",
        "重任务队列并发度（进程管理命令拉起 worker 用）",
        default=4,
        consumers=("management/commands/services/services/celery_heavy.py",),
    ),
    "CELERY_FLOWER_HOST": KS(
        "str",
        "Flower 监听地址（进程管理命令拉起 Flower 用）",
        default="127.0.0.1",
        consumers=("celery/flower.py",),
    ),
    "CELERY_FLOWER_PORT": KS("int", "Flower 监听端口", default=5566, consumers=("celery/flower.py",)),
    "CELERY_FLOWER_AUTH": KS(
        "str", "Flower Basic Auth（user:pass，空 = 不启用）", default="", consumers=("celery/flower.py",)
    ),
    # ---------------------------------------------------------------
    # 七、通知与监控
    # ---------------------------------------------------------------
    "EMAIL_HOST_USER": KS("str", "发件账号（系统邮件通知发件人兜底）", consumers=("tasks.py",), framework=True),
    "EMAIL_FROM": KS(
        "str", "发件显示名（系统邮件通知；空 = 回落 EMAIL_HOST_USER）", default="", consumers=("tasks.py",)
    ),
    "EMAIL_SUBJECT_PREFIX": KS("str", "系统邮件主题前缀", consumers=("tasks.py",), framework=True),
    "SECURITY_MONITOR_CPU_PERCENT_MAX": KS(
        "float", "CPU 使用率告警阈值（%）", default=80, consumers=("notifications.py",)
    ),
    "SECURITY_MONITOR_MEMORY_USED_MAX": KS(
        "float", "内存使用率告警阈值（%）", default=85, consumers=("notifications.py",)
    ),
    "SECURITY_MONITOR_DISK_USED_MAX": KS(
        "float", "磁盘使用率告警阈值（%）", default=80, consumers=("notifications.py",)
    ),
    "SECURITY_MONITOR_CPU_LOAD_MAX": KS(
        "float", "CPU 负载告警阈值（load1）", default=5, consumers=("notifications.py",)
    ),
    "METRICS_ENABLED": KS(
        "bool",
        "Prometheus 指标端点开关（关闭即 404 / no-op）",
        default=False,
        consumers=("api/metrics.py",),
    ),
    "METRICS_TOKEN": KS("str", "指标端点令牌（空 = 端点拒访）", default="", consumers=("api/metrics.py",)),
    "HEALTH_CHECK_SKIP_CELERY": KS(
        "bool",
        "健康检查是否跳过 celery 探针（测试/离线环境用）",
        default=False,
        consumers=("utils/health.py",),
    ),
    # ---------------------------------------------------------------
    # 八、模块裁剪
    # ---------------------------------------------------------------
    "MODULE_PRESET": KS(
        "str",
        "模块发行预设（core / standard / full；与 core/modules/registry.py 的 DEFAULT_PRESET 同值）",
        default="full",
        consumers=("core/modules/registry.py",),
    ),
    "MODULE_ENABLE": KS(
        "list[str] | tuple[str, ...]",
        "显式启用模块清单（预设之外的加白）",
        default=(),
        consumers=("core/modules/registry.py",),
    ),
    "MODULE_DISABLE": KS(
        "list[str] | tuple[str, ...]",
        "显式停用模块清单（预设之内的裁剪）",
        default=(),
        consumers=("core/modules/registry.py",),
    ),
    # ---------------------------------------------------------------
    # 九、路由与工程装配
    # ---------------------------------------------------------------
    "ROOT_URLCONF": KS("str", "根 URLConf（路由收集/OpenAPI 生成用）", consumers=("core/utils.py",)),
    "ROUTE_IGNORE_URL": KS(
        "list[str]",
        "路由收集忽略表（正则，不纳入权限点/路由树；缺省空表 = 不忽略）",
        default=[],
        consumers=("core/utils.py",),
    ),
    "XADMIN_APPS": KS(
        "list[str]",
        "二开 app 注册表（路由/WS/周期任务自动发现；缺省空表 = 不自动发现）",
        default=[],
        consumers=("core/utils.py",),
    ),
    "DB_PREFIX": KS(
        "str",
        "物理表前缀（多环境共库时的表空间隔离；空 = 不加前缀）",
        default="",
        consumers=("core/db/prefix.py",),
    ),
    # ---------------------------------------------------------------
    # 十、运行模式与操作日志
    # ---------------------------------------------------------------
    "DEBUG": KS(
        "bool",
        "调试模式（媒体文件直出、开发态分支）",
        default=False,
        consumers=("base/decorators.py", "management/commands/services/services/flower.py", "utils/media.py"),
        framework=True,
    ),
    "DEBUG_DEV": KS(
        "bool",
        "开发态增强开关（额外诊断输出/演示能力）",
        default=False,
        consumers=("base/decorators.py", "core/exception.py", "signal_handlers.py"),
    ),
    "API_LOG_ENABLE": KS(
        "bool",
        "操作日志开关（中间件写 OperationLog）",
        default=None,
        consumers=("core/middleware.py",),
    ),
    "API_LOG_METHODS": KS(
        "set[str] | list[str]",
        "纳入操作日志的 HTTP 方法",
        default=None,
        consumers=("core/middleware.py",),
    ),
    "API_LOG_IGNORE": KS(
        "dict[str, list[str]]",
        "操作日志忽略表（model label / path → 方法清单）",
        default=None,
        consumers=("core/middleware.py",),
    ),
    "API_MODEL_MAP": KS(
        "dict[str, str]",
        "操作日志模块名映射（path → 展示名，缺省空表 = 回落模型 label）",
        default={},
        consumers=("core/middleware.py",),
    ),
    "ATOMIC_REQUESTS_SKIP_READ_ACTIONS": KS(
        "bool",
        "纯读请求（GET/HEAD）免 ATOMIC_REQUESTS 事务",
        default=True,
        consumers=("core/atomic_read.py",),
    ),
    "RECYCLE_BIN_RETENTION_DAYS": KS(
        "int",
        "回收站保留天数（清理任务与回收站列表倒计时）",
        default=30,
        consumers=("core/modelset/recycle.py", "tasks.py"),
    ),
    # ---------------------------------------------------------------
    # 十一、请求上下文与审计
    # ---------------------------------------------------------------
    "TRUSTED_PROXY_IPS": KS(
        "list[str]",
        "可信反向代理地址（单个 IP 或 CIDR；空 = 不信任转发头）",
        default=[],
        consumers=("utils/request.py",),
    ),
    "AUDIT_DIFF_MODELS": KS(
        "list[str]",
        "字段级审计 diff 白名单（模型 _meta.label；空 = 关闭 diff 记录）",
        default=[],
        consumers=("core/config/conf_security.py",),
    ),
    # ---------------------------------------------------------------
    # 十二、本地化与协议
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
    """契约缺省值的文档渲染（文档表与守护测试同源，防两处口径漂移）。"""
    if entry.default is REQUIRED:
        return "必给"
    return repr(entry.default)


def required_kernel_settings() -> tuple[str, ...]:
    """必给键清单（宿主最小对接面；文档与守护同源）。"""
    return tuple(sorted(name for name, entry in KERNEL_SETTINGS.items() if entry.default is REQUIRED))


def _entry(name: str) -> KernelSetting:
    entry = KERNEL_SETTINGS.get(name)
    if entry is None:
        raise KeyError(f"{name} 未登记在 common/settings_contract.py（内核新增读取必须同步登记契约）")
    return entry


def kernel_setting(name: str) -> Any:
    """按契约读取内核 settings 键（缺失回落契约缺省值；``REQUIRED`` 键缺失即报错）。

    回落缺省值时返回**可变容器的副本**（dict / list / set），避免调用方把契约面登记的
    缺省对象当作可变状态原地改写；命中宿主配置时原样返回，保持「就地改写 settings 生效」
    的既有语义（如 auto_register_app_url 追加许可前缀）。
    """
    from django.conf import settings

    entry = _entry(name)
    if entry.default is REQUIRED:
        return kernel_required_setting(name)
    value = getattr(settings, name, _MISSING)
    if value is _MISSING:
        return copy.deepcopy(entry.default) if isinstance(entry.default, (dict, list, set)) else entry.default
    return value


def kernel_required_setting(name: str) -> Any:
    """按契约直读内核 settings 键（未登记或缺失均报 ImproperlyConfigured）。"""
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    entry = _entry(name)
    try:
        return getattr(settings, name)
    except AttributeError as exc:
        raise ImproperlyConfigured(
            f"settings.{name} 缺失：内核 settings 契约标记为必给键（用途：{entry.purpose}）"
        ) from exc
