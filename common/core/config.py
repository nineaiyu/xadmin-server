#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 12/15/2023
# 系统配置的缓存失效由信号自动处理（system/signal_handler.py），个人级继承值
# 不落个人缓存（直读系统级），系统默认变更无需手动清理缓存；
# 手动兜底命令仍可用： python manage.py expire_caches config_*


import json
import re

from django.template import Context, Template, TemplateSyntaxError
from django.template.base import VariableNode
from rest_framework import serializers

from common.cache.storage import UserSystemConfigCache
from common.utils import get_logger
from server.const import CONFIG
from system.services import SystemConfig, UserPersonalConfig

logger = get_logger(__name__)


class SystemConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemConfig
        fields = "__all__"


def get_render_context(tmp: str, context: dict) -> str:
    template = Template(tmp)
    for node in template.nodelist:
        if isinstance(node, VariableNode):
            v_key = re.findall(r"<Variable Node: (.*)>", str(node))
            if v_key and v_key[0].isupper():
                context[v_key[0]] = getattr(SysConfig, v_key[0])
    context = Context(context)
    return template.render(context)


class ConfigCacheBase:
    # 无行 no_row 标记 TTL：只缓存「该 key 无数据行」这一事实（值不落缓存，
    # 每次由调用方默认值现算），信号失效/种子导入/行创建路径都会清理标记，
    # 绕过信号的 ORM 直建行在该窗口内自愈
    ABSENCE_CACHE_TIMEOUT = 60

    def __init__(
        self,
        px="system",
        model=SystemConfig,
        cache=UserSystemConfigCache,
        serializer=SystemConfigSerializer,
        timeout=60 * 60 * 24 * 30,
        filter_kwargs=None,
    ):
        if filter_kwargs is None:
            filter_kwargs = {}
        self.px = px
        self.model = model
        self.cache = cache
        self.timeout = timeout
        self.serializer = serializer
        self.filter_kwargs = filter_kwargs

    def invalid_config_cache(self, key="*"):
        UserSystemConfigCache(f"{self.px}_{key}").del_many()

    def get_render_value(self, value: str) -> dict:
        if value:
            try:
                context_dict = {}
                for sys_obj_dict in self.model.objects.filter(is_active=True).values().all():
                    str_value = json.dumps(sys_obj_dict["value"])  # 将dict转换为json字符串进行匹配
                    if re.findall("{{{{.*{}.*}}}}".format(sys_obj_dict["key"]), str_value):
                        logger.warning("get same render key. so continue")
                        continue
                    context_dict[sys_obj_dict["key"]] = str_value
                try:
                    value = get_render_context(value, context_dict)
                except TemplateSyntaxError as e:
                    res_list = re.findall("Could not parse the remainder: '{{(.*?)}}'", str(e))
                    for res in res_list:
                        r_value = self.get_render_value(f"{{{{{res}}}}}")
                        value = value.replace(f"{{{{{res}}}}}", f"{r_value}")
                    value = self.get_render_value(value)
                except Exception as e:
                    logger.warning(f"db config - render failed {e}")
            except Exception as e:
                logger.warning(f"db config - render failed {e}")
        value = value.replace('"(', "").replace(')"', "")  # 支持"({{ h }})"， 为了转换变量，h不能为字符串
        try:
            value = json.loads(value)
        except Exception as e:
            logger.warning(f"db config - json loads failed {e}")
        # if isinstance(value, str):
        #     if value.isdigit():
        #         return int(value)
        #     v_group = re.findall('"(.*?)"', value)
        #     if v_group and len(v_group) == 1 and v_group[0].isdigit():
        #         return int(v_group[0])
        return value

    def get_value_from_db(self, key):  # 取得数据是激活的数据，如果数据未激活，则取默认数据
        row = self.model.objects.filter(is_active=True, key=key, **self.filter_kwargs).first()
        if row is None:
            return {}
        data = self.serializer(row).data
        if re.findall("{{{{.*{}.*}}}}".format(data["key"]), json.dumps(data["value"])):  # 防止渲染出现递归
            logger.warning(f"get same render key:{key}. so get default value")
            data["key"] = ""
        return data

    def get_default_data(self, key, default_data):
        if default_data is None:
            default_data = {}
        return default_data

    def get_value(self, key, default_data=None, ignore_access=True):
        data = self.get_data(key, default_data, ignore_access)
        if data:
            return data.get("value")
        return data

    def get_data(self, key, default_data=None, ignore_access=True):
        cache = self.cache(f"{self.px}_{key}")
        cache_data = cache.get_storage_cache()
        if cache_data is not None and cache_data.get("key", "") == key:
            if cache_data.get("no_row"):
                return self._absence_value(key, default_data)
            if ignore_access or cache_data.get("access"):
                return cache_data
        db_data = self.get_value_from_db(key)
        if db_data.get("key") != key:
            # 无行：缓存 no_row 标记（短 TTL）——既不把空值/默认值固化进缓存
            # （default_data 由调用方每次给定），也避免无行键每次读都查库
            cache.set_storage_cache({"key": key, "no_row": True}, timeout=self.ABSENCE_CACHE_TIMEOUT)
            return self._absence_value(key, default_data)
        db_data["value"] = self.get_render_value(json.dumps(db_data["value"]))
        cache.set_storage_cache(db_data, timeout=self.timeout)
        if ignore_access or db_data.get("access"):
            return db_data
        return {}

    def _absence_value(self, key, default_data):
        """无行（系统级/用户级通用）时的返回：调用方默认值的纯 JSON 拷贝。

        不做模板渲染——无行场景下 {{ KEY }} 引用的源行同样不存在，渲染只会
        白白多一次全表查询；default_data 为 None 时返回空 {}（缺席语义自担）。
        """
        if default_data is None:
            return {}
        return {"key": key, "value": json.loads(json.dumps(default_data)), "access": True}

    def save_db(self, key, value, is_active, description, **kwargs):
        defaults = {"value": value}
        if is_active is not None:
            defaults["is_active"] = is_active
        if description is not None:
            defaults["description"] = description
        return self.model.objects.update_or_create(key=key, defaults=defaults, **kwargs)

    def delete_db(self, key, **kwargs):
        return self.model.objects.filter(key=key, **kwargs).delete()

    def set_value(self, key, value, is_active=None, description=None, **kwargs):
        obj = self.save_db(key, value, is_active, description, **kwargs)
        self.cache(f"{self.px}_{key}").del_storage_cache()
        return obj

    def set_default_value(self, key, **kwargs):
        return self.set_value(key, self.get_value(key, None), **kwargs)

    def del_value(self, key, **kwargs):
        self.delete_db(key, **kwargs)
        self.cache(f"{self.px}_{key}").del_storage_cache()

    def __getattribute__(self, name):
        if name == "shape":
            return ""
        try:
            return object.__getattribute__(self, name)
        except AttributeError as e:
            # 属性访问即"读同名配置"是本类的设计；此处只兜底 AttributeError，
            # 避免把 property 内部的真实异常（TypeError/KeyError 等）也吞成"读配置"
            logger.debug(f"__getattribute__ fallback to config. name:{name} error:{e}")
            return self.get_value(name)


class BaseConfCache(ConfigCacheBase):
    """系统级配置读取（键 → 值）。

    默认值单一来源：全部回读 ``server/conf.py`` 的静态配置实例 ``CONFIG``
    （即 config.yml / 环境变量的值或代码默认值），本类不再硬编码任何默认值；
    ``loadjson/systemconfig.json`` 的种子初值须与 conf.py 一致（守护测试校验）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def FILE_UPLOAD_SIZE(self):
        return self.get_value("FILE_UPLOAD_SIZE", CONFIG.FILE_UPLOAD_SIZE)

    @property
    def PICTURE_UPLOAD_SIZE(self):
        return self.get_value("PICTURE_UPLOAD_SIZE", CONFIG.PICTURE_UPLOAD_SIZE)

    @property
    def OPERATION_LOG_RETENTION_DAYS(self):
        """操作日志保留天数（清理保留期配置化，默认 180 天）"""
        return self.get_value("OPERATION_LOG_RETENTION_DAYS", CONFIG.OPERATION_LOG_RETENTION_DAYS)

    @property
    def SEARCH_CHOICES_MAX_COUNT(self):
        """search-columns / search-fields 关联列 choices 的最大返回条数（默认 200）。

        大表关联字段请务必自定义 input_type='api-search-*'（远程搜索），否则超出的
        选项不会出现在下拉里，且接口会带出 choices_truncated 标记。
        """
        return self.get_value("SEARCH_CHOICES_MAX_COUNT", CONFIG.SEARCH_CHOICES_MAX_COUNT)

    @property
    def SLOW_REQUEST_THRESHOLD(self):
        """慢请求阈值（秒）：超阈值打 warning 日志，监控面板 slow 接口同口径（默认 1.0）。"""
        return float(self.get_value("SLOW_REQUEST_THRESHOLD", CONFIG.SLOW_REQUEST_THRESHOLD))

    @property
    def OPERATION_LOG_ERROR_RETENTION_DAYS(self):
        """错误操作日志（status_code != 1000）额外保留天数（默认 365；0/空 = 跟随全量保留期）。"""
        return self.get_value("OPERATION_LOG_ERROR_RETENTION_DAYS", CONFIG.OPERATION_LOG_ERROR_RETENTION_DAYS)

    @property
    def SENSITIVE_OPERATION_METHODS(self):
        """敏感操作告警的 HTTP 方法清单（默认 ["DELETE"]；"ALL" 或空表示不按方法过滤）。"""
        return self.get_value("SENSITIVE_OPERATION_METHODS", CONFIG.SENSITIVE_OPERATION_METHODS)

    @property
    def SENSITIVE_OPERATION_PATHS(self):
        """敏感操作告警的路径正则清单（默认空 = 不按路径过滤，与方法清单 AND 组合）。"""
        return self.get_value("SENSITIVE_OPERATION_PATHS", CONFIG.SENSITIVE_OPERATION_PATHS)

    @property
    def APPROVAL_REQUIRED_PATHS(self):
        """敏感操作审批拦截的路径正则清单（默认空 = 审批整体休眠，渐进启用）。

        仅对显式挂载 ApprovalRequired 装饰器的 action 生效；命中清单的请求需先
        经审批中心通过后携令牌重发（一次性通行令牌）。
        """
        return self.get_value("APPROVAL_REQUIRED_PATHS", CONFIG.APPROVAL_REQUIRED_PATHS)

    @property
    def APPROVAL_APPROVER_ROLES(self):
        """审批人角色 code 清单（默认空 = 全部在用超管；申请人始终不能自审）。"""
        return self.get_value("APPROVAL_APPROVER_ROLES", CONFIG.APPROVAL_APPROVER_ROLES)

    @property
    def APPROVAL_APPROVER_PERMS(self):
        """审批人职能权限码清单（"动作:组件名"，默认空 = 不按权限反查）。

        与 APPROVAL_APPROVER_ROLES 取并集，两者皆空回退全部在用超管；持有任一
        权限码的在用用户视为职能审批人（system.services.get_users_by_perm 反查，
        借鉴 jumpserver 按权限反查角色的审批人推导模式）。
        """
        return self.get_value("APPROVAL_APPROVER_PERMS", CONFIG.APPROVAL_APPROVER_PERMS)

    @property
    def APPROVAL_TOKEN_TTL(self):
        """审批通过后令牌有效期（秒，默认 300）：有效期内携令牌重发一次有效。"""
        return int(self.get_value("APPROVAL_TOKEN_TTL", CONFIG.APPROVAL_TOKEN_TTL))

    @property
    def APPROVAL_PENDING_TIMEOUT(self):
        """待审批单超时天数（默认 3 天）：超时由清理任务置 EXPIRED。"""
        return int(self.get_value("APPROVAL_PENDING_TIMEOUT", CONFIG.APPROVAL_PENDING_TIMEOUT))

    @property
    def APPROVAL_KEEP_DAYS(self):
        """审批单保留天数（默认 180）：超过由清理任务分批删除。"""
        return int(self.get_value("APPROVAL_KEEP_DAYS", CONFIG.APPROVAL_KEEP_DAYS))

    @property
    def APPROVAL_REMIND_HOURS(self):
        """待审批超时提醒阈值（小时，默认 24；0 = 不提醒）。

        由每日提醒任务对「PENDING 且已超过该时长且未提醒过」的单向审批人补发一次提醒。
        """
        return int(self.get_value("APPROVAL_REMIND_HOURS", CONFIG.APPROVAL_REMIND_HOURS))

    @property
    def APPROVAL_FLOW_KEEP_DAYS(self):
        """流程实例保留天数（默认 365）：超过由清理任务分批删除（级联节点任务）。"""
        return int(self.get_value("APPROVAL_FLOW_KEEP_DAYS", CONFIG.APPROVAL_FLOW_KEEP_DAYS))

    @property
    def LEAVE_APPROVAL_FLOW_CODE(self):
        """请假审批流程 code（默认 leave）：请假单提交时绑定的流程定义。

        该 code 的流程不存在或未启用时，按「leave_<请假类型>」再回退「leave 前缀的
        启用流程」查找（见 system/utils/leave.py:resolve_leave_flow），全找不到则拒绝
        提交并提示管理员配置流程。
        """
        return self.get_value("LEAVE_APPROVAL_FLOW_CODE", CONFIG.LEAVE_APPROVAL_FLOW_CODE)

    @property
    def FILE_OFFICE_PREVIEW_ENABLED(self):
        """Office 在线预览开关（默认开）：关闭或未装 LibreOffice 时按不支持降级。"""
        return self.get_value("FILE_OFFICE_PREVIEW_ENABLED", CONFIG.FILE_OFFICE_PREVIEW_ENABLED)

    @property
    def FILE_OFFICE_MAX_BYTES(self):
        """Office 预览转换大小上限（字节，默认 20MB）：超过不转换（保护转换进程）。"""
        return int(self.get_value("FILE_OFFICE_MAX_BYTES", CONFIG.FILE_OFFICE_MAX_BYTES))

    @property
    def FILE_OFFICE_CONVERT_TIMEOUT(self):
        """LibreOffice 单次转换超时（秒，默认 60）：超时杀进程并降级为不可预览。"""
        return int(self.get_value("FILE_OFFICE_CONVERT_TIMEOUT", CONFIG.FILE_OFFICE_CONVERT_TIMEOUT))

    @property
    def FILE_OFFICE_WAIT_SECONDS(self):
        """请求侧等待转换产物的窗口（秒，默认 8）：未等到返回 1006 由前端重试。"""
        return int(self.get_value("FILE_OFFICE_WAIT_SECONDS", CONFIG.FILE_OFFICE_WAIT_SECONDS))

    @property
    def FILE_OFFICE_SOFFICE_BIN(self):
        """LibreOffice 可执行文件路径（默认空 = 自动探测 PATH 与常见安装路径）。"""
        return self.get_value("FILE_OFFICE_SOFFICE_BIN", CONFIG.FILE_OFFICE_SOFFICE_BIN)

    @property
    def SCIM_ENABLED(self):
        """SCIM 用户目录同步总开关（默认关，S1）：开启且配置 SCIM_TOKEN 后生效。"""
        return self.get_value("SCIM_ENABLED", CONFIG.SCIM_ENABLED)

    @property
    def SCIM_TOKEN(self):
        """SCIM 独立 Bearer Token（默认空 = 未配置，任何请求 401；access=false 不对外回传）。"""
        return self.get_value("SCIM_TOKEN", CONFIG.SCIM_TOKEN)

    @property
    def SCIM_RATE_LIMIT(self):
        """SCIM 凭证级限流速率（SimpleRateThrottle 速率串，默认 600/min；空或 0 = 不限）。"""
        return self.get_value("SCIM_RATE_LIMIT", CONFIG.SCIM_RATE_LIMIT)

    @property
    def SCIM_DEFAULT_ROLE_CODE(self):
        """SCIM 新建用户的默认角色 code（默认空 = 不分配角色，由 IdP 分组另行下发）。"""
        return self.get_value("SCIM_DEFAULT_ROLE_CODE", CONFIG.SCIM_DEFAULT_ROLE_CODE)

    @property
    def BACKUP_ALERT_TOKEN(self):
        """备份失败告警回调令牌（默认空 = 端点未启用；access=false 不对外回传）。"""
        return self.get_value("BACKUP_ALERT_TOKEN", CONFIG.BACKUP_ALERT_TOKEN)

    @property
    def CSP_MODE(self):
        """CSP 模式（S3，默认 report-only 观察期）：disabled / report-only / enforce。"""
        return self.get_value("CSP_MODE", CONFIG.CSP_MODE)

    @property
    def CSP_REPORT_URI(self):
        """CSP 违规上报地址（默认空 = 不下发 report-uri）：一般指向 /api/common/api/csp-report。"""
        return self.get_value("CSP_REPORT_URI", CONFIG.CSP_REPORT_URI)

    @property
    def PAT_RATE_LIMIT(self):
        """PAT 凭证级限流速率（SimpleRateThrottle 速率串，默认 60/min；空或 0 = 不限）。"""
        return self.get_value("PAT_RATE_LIMIT", CONFIG.PAT_RATE_LIMIT)

    @property
    def FILE_STORAGE_QUOTA_MB(self):
        """个人文件存储配额（MB，默认 0 = 不限）：上传前按 creator 聚合校验。"""
        return int(self.get_value("FILE_STORAGE_QUOTA_MB", CONFIG.FILE_STORAGE_QUOTA_MB))

    @property
    def FILE_KEEP_DAYS(self):
        """正式上传文件保留天数（默认 0 = 不清理）。

        仅清理「非临时、无业务引用」的历史文件；物理文件删除由磁盘引用守护兜底。
        """
        return int(self.get_value("FILE_KEEP_DAYS", CONFIG.FILE_KEEP_DAYS))

    @property
    def FILE_UPLOAD_COUNT_LIMIT(self):
        """个人上传文件数量上限（默认 0 = 不限）：上传前按 creator 计数校验。"""
        return int(self.get_value("FILE_UPLOAD_COUNT_LIMIT", CONFIG.FILE_UPLOAD_COUNT_LIMIT))

    @property
    def FILE_PREVIEW_TEXT_MAX_BYTES(self):
        """文本预览读取上限（字节，默认 256KB）：超过即截断并提示下载查看。

        避免把一个几百 MB 的日志整份读进内存再回给浏览器。
        """
        return int(self.get_value("FILE_PREVIEW_TEXT_MAX_BYTES", CONFIG.FILE_PREVIEW_TEXT_MAX_BYTES))

    @property
    def FILE_PREVIEW_THUMB_WIDTH(self):
        """列表缩略图宽度（像素，默认 240）：按原图比例等比缩放，不拉伸。"""
        return int(self.get_value("FILE_PREVIEW_THUMB_WIDTH", CONFIG.FILE_PREVIEW_THUMB_WIDTH))

    @property
    def FILE_PREVIEW_IMAGE_WIDTH(self):
        """抽屉大图宽度（像素，默认 1280）：原图更小时不放大。"""
        return int(self.get_value("FILE_PREVIEW_IMAGE_WIDTH", CONFIG.FILE_PREVIEW_IMAGE_WIDTH))

    @property
    def OAUTH_PROVIDERS(self):
        """第三方登录 provider 列表（JSON 数组，默认空 = 整体休眠）。

        每项结构见 `system/utils/oauth.py`：key/name/enabled/client_id/client_secret/
        authorize_url/token_url/userinfo_url/scope/subject_field/auto_create。
        密钥仅服务端可见，列表接口回传时掩码（见 `mask_providers`）。
        """
        return self.get_value("OAUTH_PROVIDERS", CONFIG.OAUTH_PROVIDERS)

    @property
    def FILE_PREVIEW_CACHE_KEEP_DAYS(self):
        """预览缓存保留天数（默认 7）：缓存是派生产物，过期删除后按需重建。"""
        return int(self.get_value("FILE_PREVIEW_CACHE_KEEP_DAYS", CONFIG.FILE_PREVIEW_CACHE_KEEP_DAYS))

    @property
    def AUDIT_DIFF_MODELS(self):
        """字段级审计 diff 白名单（模型 _meta.label JSON 清单，默认空 = 关闭）。

        优先读系统配置（管理员可运行时扩容），未登记时回退 django settings
        （config.yml 链路 / settings_e2e.py 的 AUDIT_DIFF_MODELS 仍然生效）。
        注意必须用 django.conf.settings 惰性对象：静态 conf 链（CONFIG）读不到
        settings_e2e 尾部的显式覆盖；本键是唯一需要与测试覆盖联动的例外，
        SysConfig 其余键的默认值统一单源在 server/conf.py（见 BaseConfCache 说明）。
        """
        from django.conf import settings as dj_settings

        return self.get_value("AUDIT_DIFF_MODELS", getattr(dj_settings, "AUDIT_DIFF_MODELS", []) or [])

    @property
    def EXPORT_FILE_KEEP_DAYS(self):
        """异步导出记录与产物保留天数（下载中心，默认 7 天）。"""
        return int(self.get_value("EXPORT_FILE_KEEP_DAYS", CONFIG.EXPORT_FILE_KEEP_DAYS))

    @property
    def EXPORT_ASYNC_MAX_RUNNING(self):
        """同一用户同时进行中的异步导出任务上限（默认 3；0 表示不限制）。"""
        return int(self.get_value("EXPORT_ASYNC_MAX_RUNNING", CONFIG.EXPORT_ASYNC_MAX_RUNNING))

    @property
    def MONITOR_RETENTION_DAYS(self):
        """主机监控心跳历史保留天数（common.Monitor 30s 一条，默认 30 天）。"""
        return int(self.get_value("MONITOR_RETENTION_DAYS", CONFIG.MONITOR_RETENTION_DAYS))

    @property
    def SESSION_ONLINE_TIMEOUT(self):
        """纯 HTTP 会话的在线判定窗口（秒）：last_active 超过该窗口视为离线（默认 300）。"""
        return int(self.get_value("SESSION_ONLINE_TIMEOUT", CONFIG.SESSION_ONLINE_TIMEOUT))

    @property
    def USER_SESSION_RETENTION_DAYS(self):
        """已结束会话记录保留天数（在线用户/会话管理，默认 30 天）。"""
        return int(self.get_value("USER_SESSION_RETENTION_DAYS", CONFIG.USER_SESSION_RETENTION_DAYS))

    @property
    def IMPORT_RECORD_KEEP_DAYS(self):
        """异步导入记录、源文件与错误报告保留天数（下载中心，默认 30 天）。"""
        return int(self.get_value("IMPORT_RECORD_KEEP_DAYS", CONFIG.IMPORT_RECORD_KEEP_DAYS))

    @property
    def CHAT_HISTORY_DAYS(self):
        """聊天消息保留天数（二期，默认 0 = 不清理）。

        超过保留期的消息由每日清理任务分批删除；会话与成员关系保留，
        历史清空的会话在列表里仅摘要为空。
        """
        return int(self.get_value("CHAT_HISTORY_DAYS", CONFIG.CHAT_HISTORY_DAYS))

    @property
    def IMPORT_FAIL_RATE_LIMIT(self):
        """异步导入失败率中止阈值（默认 0.5；0 表示不按失败率中止）。"""
        return float(self.get_value("IMPORT_FAIL_RATE_LIMIT", CONFIG.IMPORT_FAIL_RATE_LIMIT))

    @property
    def IMPORT_ASYNC_MAX_RUNNING(self):
        """同一用户同时进行中的异步导入任务上限（默认 3；0 表示不限制）。"""
        return int(self.get_value("IMPORT_ASYNC_MAX_RUNNING", CONFIG.IMPORT_ASYNC_MAX_RUNNING))

    @property
    def IMPORT_VALIDATE_ERROR_LIMIT(self):
        """导入前校验返回的错误行明细上限（默认 200，超出截断并标记）。"""
        return int(self.get_value("IMPORT_VALIDATE_ERROR_LIMIT", CONFIG.IMPORT_VALIDATE_ERROR_LIMIT))


class MessagePushConfCache(ConfigCacheBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def PUSH_MESSAGE_NOTICE(self):
        return self.get_value("PUSH_MESSAGE_NOTICE", CONFIG.PUSH_MESSAGE_NOTICE)

    @property
    def PUSH_CHAT_MESSAGE(self):
        return self.get_value("PUSH_CHAT_MESSAGE", CONFIG.PUSH_CHAT_MESSAGE)


class ConfigCache(BaseConfCache, MessagePushConfCache):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


SysConfig = ConfigCache()


def batch_user_config(user_pks, key, default=None):
    """批量读取多个用户的同一配置项，返回 {user_pk: value}。

    逐用户 UserConfig(pk).key 会产生 N 次缓存读 + N 次 DB 回源；这里一次 get_many
    批量取缓存，缺失项只回源一次系统级默认值。系统公告/性能告警等全量推送场景
    由 N 次降为常数次。
    """
    from django.core.cache import cache as django_cache

    pks = list(dict.fromkeys(user_pks))
    if not pks:
        return {}
    key_map = {pk: UserSystemConfigCache(f"user_{pk}_{key}").cache_key for pk in pks}
    cached = django_cache.get_many(list(key_map.values()))
    result = {}
    for pk, cache_key in key_map.items():
        data = cached.get(cache_key)
        # no_row 缺席标记只表示「无个人行」，值需回退系统级，不能当作已缓存值
        if isinstance(data, dict) and data.get("key") == key and not data.get("no_row"):
            result[pk] = data.get("value")
    missing = [pk for pk in pks if pk not in result]
    if missing:
        # 缓存 miss（含 no_row 缺席标记与刚写入未回填的个人行）：一次 IN 查询
        # 回查个人行，有行用行值，仍无行才回退系统级默认值（单次读取）
        personal = dict(
            UserPersonalConfig.objects.filter(key=key, owner_id__in=missing, is_active=True).values_list(
                "owner_id", "value"
            )
        )
        system_value = SysConfig.get_value(key, default)
        for pk in missing:
            result[pk] = personal[pk] if pk in personal else system_value
    return result


class UserConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPersonalConfig
        fields = "__all__"


class UserPersonalConfigCache(ConfigCache):
    def __init__(self, user_obj):
        self.user_obj = user_obj
        self.filter_kwargs = {"owner": self.user_obj}
        if isinstance(user_obj, (str, int)):
            key = user_obj
            self.filter_kwargs = {"owner_id": self.user_obj}
        else:
            key = user_obj.pk
        super().__init__(
            f"user_{key}",
            UserPersonalConfig,
            UserSystemConfigCache,
            UserConfigSerializer,
            filter_kwargs=self.filter_kwargs,
        )

    def _absence_value(self, key, default_data):
        """无个人行时的读取结果：系统生效值，统一带 system_fallback 标记。

        三级回退语义（L0 conf 默认 / L1 系统行 / L2 个人行）：L1/L2 之间不做
        inherit 阈值判断——系统行值即全员当前默认，个人行缺席时用户理应读到它；
        inherit 字段保留为「键允许个人覆盖」的元数据，不再参与读取链。
        system_fallback 标记供 get_personal_config_data 区分真实个人行。

        default_data 非 None 时 SysConfig.get_data 必返回非空（有行→行数据，
        无行→默认值结构），因此这里无需 default 兜底分支——也不做模板渲染：
        self.model 是个人配置表，get_render_value 会全表扫描它。
        """
        data = SysConfig.get_data(key, default_data)
        if data and data.get("key") == key:
            return {"key": key, "value": data.get("value"), "access": True, "system_fallback": True}
        return {}

    def get_data(self, key, default_data=None, ignore_access=True):
        """用户级读取：个人缓存槽只存「个人行值（长 TTL）」或「缺席标记（短 TTL）」。

        继承来的系统值不落个人缓存，缺席标记只缓存「该用户没有此 key 的个人行」
        这一事实，值本身每次直读系统级缓存（system_{key}）：系统默认变更只需
        失效系统级缓存，即可对所有未个性化用户即时生效，无需失效任何用户 key；
        已有个人行的用户读自己的值，不受系统级变更影响。
        """
        cache = self.cache(f"{self.px}_{key}")
        cache_data = cache.get_storage_cache()
        if cache_data is not None and cache_data.get("key", "") == key:
            if cache_data.get("no_row"):
                return self._absence_value(key, default_data)
            if "inherit" in cache_data:
                # 旧版缓存把继承的系统行数据（含 inherit 字段，个人行模型没有
                # 该字段）存进了个人槽，且系统行更新信号不清理用户槽——继续
                # 命中会把用户的配置值冻结在旧值上（最长一个缓存 TTL）。视同
                # 缺席并清理，升级后自动收敛
                cache.del_storage_cache()
                return self._absence_value(key, default_data)
            if ignore_access or cache_data.get("access"):
                return cache_data
        db_data = self.get_value_from_db(key)
        if db_data.get("key") != key:
            cache.set_storage_cache({"key": key, "no_row": True}, timeout=self.ABSENCE_CACHE_TIMEOUT)
            return self._absence_value(key, default_data)
        db_data["value"] = self.get_render_value(json.dumps(db_data["value"]))
        cache.set_storage_cache(db_data, timeout=self.timeout)
        if ignore_access or db_data.get("access"):
            return db_data
        return {}

    def delete_db(self, key, **kwargs):
        return super().delete_db(key, **self.filter_kwargs)

    def save_db(self, key, value, is_active=None, description=None, **kwargs):
        return super().save_db(key, value, is_active, description, **self.filter_kwargs, **kwargs)

    def set_default_value(self, key, **kwargs):
        return super().set_default_value(key, **self.filter_kwargs)


UserConfig = UserPersonalConfigCache


def get_personal_config_data(user_obj, key):
    """返回用户真实个人行的完整缓存数据（无个人行返回 None）。

    缺席标记/系统生效值回退（no_row/system_fallback）均视为「未个性化」，
    返回 None；供配额「个人行优先、缺席继承系统级」语义使用。
    """
    data = UserConfig(user_obj).get_data(key, None)
    is_personal = isinstance(data, dict) and data.get("key") == key
    if is_personal and not data.get("no_row") and not data.get("system_fallback"):
        return data
    return None


def get_personal_int_config(user_obj, key, system_value):
    """个人级 int 配置读取：真实个人行优先（int 类型才生效），否则回退系统级。"""
    data = get_personal_config_data(user_obj, key)
    if data is not None and isinstance(data.get("value"), int):
        return data["value"]
    return system_value
