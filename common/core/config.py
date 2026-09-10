#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 12/15/2023
# 修改下面配置之后，记得清理一下redis缓存： python manage.py expire_caches 'config_*'


import json
import re

from django.template import Context, Template, TemplateSyntaxError
from django.template.base import VariableNode
from rest_framework import serializers

from common.cache.storage import UserSystemConfigCache
from common.utils import get_logger
from server import settings
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


class ConfigCacheBase(object):
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
                    if re.findall("{{.*%s.*}}" % sys_obj_dict["key"], str_value):
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
        data = self.serializer(self.model.objects.filter(is_active=True, key=key, **self.filter_kwargs).first()).data
        if re.findall("{{.*%s.*}}" % data["key"], json.dumps(data["value"])):  # 防止渲染出现递归
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
            if ignore_access or cache_data.get("access"):
                return cache_data
        db_data = self.get_value_from_db(key)
        d_key = db_data.get("key", "")
        if d_key != key:
            data = self.get_default_data(key, default_data)
            if data is not None:
                db_data["value"] = data
                db_data["key"] = key
                db_data["access"] = True
        db_data["value"] = self.get_render_value(json.dumps(db_data["value"]))
        cache.set_storage_cache(db_data, timeout=self.timeout)
        if ignore_access or db_data.get("access"):
            return db_data
        return {}

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
    def __init__(self, *args, **kwargs):
        super(BaseConfCache, self).__init__(*args, **kwargs)

    @property
    def FILE_UPLOAD_SIZE(self):
        return self.get_value("FILE_UPLOAD_SIZE", settings.FILE_UPLOAD_SIZE)

    @property
    def PICTURE_UPLOAD_SIZE(self):
        return self.get_value("PICTURE_UPLOAD_SIZE", settings.PICTURE_UPLOAD_SIZE)

    @property
    def OPERATION_LOG_RETENTION_DAYS(self):
        """操作日志保留天数（清理保留期配置化，默认 180 天）"""
        return self.get_value("OPERATION_LOG_RETENTION_DAYS", 30 * 6)

    @property
    def SEARCH_CHOICES_MAX_COUNT(self):
        """search-columns / search-fields 关联列 choices 的最大返回条数（默认 200）。

        大表关联字段请务必自定义 input_type='api-search-*'（远程搜索），否则超出的
        选项不会出现在下拉里，且接口会带出 choices_truncated 标记。
        """
        return self.get_value("SEARCH_CHOICES_MAX_COUNT", 200)

    @property
    def SLOW_REQUEST_THRESHOLD(self):
        """慢请求阈值（秒）：超阈值打 warning 日志，监控面板 slow 接口同口径（默认 1.0）。"""
        return float(self.get_value("SLOW_REQUEST_THRESHOLD", 1.0))

    @property
    def OPERATION_LOG_ERROR_RETENTION_DAYS(self):
        """错误操作日志（status_code != 1000）额外保留天数（默认 365；0/空 = 跟随全量保留期）。"""
        return self.get_value("OPERATION_LOG_ERROR_RETENTION_DAYS", 365)

    @property
    def SENSITIVE_OPERATION_METHODS(self):
        """敏感操作告警的 HTTP 方法清单（默认 ["DELETE"]；"ALL" 或空表示不按方法过滤）。"""
        return self.get_value("SENSITIVE_OPERATION_METHODS", ["DELETE"])

    @property
    def SENSITIVE_OPERATION_PATHS(self):
        """敏感操作告警的路径正则清单（默认空 = 不按路径过滤，与方法清单 AND 组合）。"""
        return self.get_value("SENSITIVE_OPERATION_PATHS", [])

    @property
    def APPROVAL_REQUIRED_PATHS(self):
        """敏感操作审批拦截的路径正则清单（默认空 = 审批整体休眠，渐进启用）。

        仅对显式挂载 ApprovalRequired 装饰器的 action 生效；命中清单的请求需先
        经审批中心通过后携令牌重发（一次性通行令牌）。
        """
        return self.get_value("APPROVAL_REQUIRED_PATHS", [])

    @property
    def APPROVAL_APPROVER_ROLES(self):
        """审批人角色 code 清单（默认空 = 全部在用超管；申请人始终不能自审）。"""
        return self.get_value("APPROVAL_APPROVER_ROLES", [])

    @property
    def APPROVAL_TOKEN_TTL(self):
        """审批通过后令牌有效期（秒，默认 300）：有效期内携令牌重发一次有效。"""
        return int(self.get_value("APPROVAL_TOKEN_TTL", 300))

    @property
    def APPROVAL_PENDING_TIMEOUT(self):
        """待审批单超时天数（默认 3 天）：超时由清理任务置 EXPIRED。"""
        return int(self.get_value("APPROVAL_PENDING_TIMEOUT", 3))

    @property
    def APPROVAL_KEEP_DAYS(self):
        """审批单保留天数（默认 180）：超过由清理任务分批删除。"""
        return int(self.get_value("APPROVAL_KEEP_DAYS", 180))

    @property
    def APPROVAL_REMIND_HOURS(self):
        """待审批超时提醒阈值（小时，默认 24；0 = 不提醒）。

        由每日提醒任务对「PENDING 且已超过该时长且未提醒过」的单向审批人补发一次提醒。
        """
        return int(self.get_value("APPROVAL_REMIND_HOURS", 24))

    @property
    def PAT_RATE_LIMIT(self):
        """PAT 凭证级限流速率（SimpleRateThrottle 速率串，默认 60/min；空或 0 = 不限）。"""
        return self.get_value("PAT_RATE_LIMIT", "60/min")

    @property
    def FILE_STORAGE_QUOTA_MB(self):
        """个人文件存储配额（MB，默认 0 = 不限）：上传前按 creator 聚合校验。"""
        return int(self.get_value("FILE_STORAGE_QUOTA_MB", 0))

    @property
    def FILE_UPLOAD_COUNT_LIMIT(self):
        """个人上传文件数量上限（默认 0 = 不限）：上传前按 creator 计数校验。"""
        return int(self.get_value("FILE_UPLOAD_COUNT_LIMIT", 0))

    @property
    def AUDIT_DIFF_MODELS(self):
        """字段级审计 diff 白名单（模型 _meta.label JSON 清单，默认空 = 关闭）。

        优先读系统配置（管理员可运行时扩容），未登记时回退 django settings
        （config.yml 链路 / settings_e2e.py 的 AUDIT_DIFF_MODELS 仍然生效）。
        注意必须用 django.conf.settings 惰性对象：本模块顶部的 `from server
        import settings` 是 conf 静态模块，读不到 settings_e2e 尾部的显式覆盖。
        """
        from django.conf import settings as dj_settings

        return self.get_value("AUDIT_DIFF_MODELS", getattr(dj_settings, "AUDIT_DIFF_MODELS", []) or [])

    @property
    def EXPORT_FILE_KEEP_DAYS(self):
        """异步导出记录与产物保留天数（下载中心，默认 7 天）。"""
        return int(self.get_value("EXPORT_FILE_KEEP_DAYS", getattr(settings, "EXPORT_FILE_KEEP_DAYS", 7)))

    @property
    def EXPORT_ASYNC_MAX_RUNNING(self):
        """同一用户同时进行中的异步导出任务上限（默认 3；0 表示不限制）。"""
        return int(self.get_value("EXPORT_ASYNC_MAX_RUNNING", 3))

    @property
    def MONITOR_RETENTION_DAYS(self):
        """主机监控心跳历史保留天数（common.Monitor 30s 一条，默认 30 天）。"""
        return int(self.get_value("MONITOR_RETENTION_DAYS", 30))

    @property
    def SESSION_ONLINE_TIMEOUT(self):
        """纯 HTTP 会话的在线判定窗口（秒）：last_active 超过该窗口视为离线（默认 300）。"""
        return int(self.get_value("SESSION_ONLINE_TIMEOUT", 300))

    @property
    def USER_SESSION_RETENTION_DAYS(self):
        """已结束会话记录保留天数（在线用户/会话管理，默认 30 天）。"""
        return int(self.get_value("USER_SESSION_RETENTION_DAYS", 30))

    @property
    def IMPORT_RECORD_KEEP_DAYS(self):
        """异步导入记录、源文件与错误报告保留天数（下载中心，默认 30 天）。"""
        return int(self.get_value("IMPORT_RECORD_KEEP_DAYS", 30))

    @property
    def IMPORT_FAIL_RATE_LIMIT(self):
        """异步导入失败率中止阈值（默认 0.5；0 表示不按失败率中止）。"""
        return float(self.get_value("IMPORT_FAIL_RATE_LIMIT", 0.5))

    @property
    def IMPORT_ASYNC_MAX_RUNNING(self):
        """同一用户同时进行中的异步导入任务上限（默认 3；0 表示不限制）。"""
        return int(self.get_value("IMPORT_ASYNC_MAX_RUNNING", 3))

    @property
    def IMPORT_VALIDATE_ERROR_LIMIT(self):
        """导入前校验返回的错误行明细上限（默认 200，超出截断并标记）。"""
        return int(self.get_value("IMPORT_VALIDATE_ERROR_LIMIT", 200))


class MessagePushConfCache(ConfigCacheBase):
    def __init__(self, *args, **kwargs):
        super(MessagePushConfCache, self).__init__(*args, **kwargs)

    @property
    def PUSH_MESSAGE_NOTICE(self):
        return self.get_value("PUSH_MESSAGE_NOTICE", True)

    @property
    def PUSH_CHAT_MESSAGE(self):
        return self.get_value("PUSH_CHAT_MESSAGE", True)


class ConfigCache(BaseConfCache, MessagePushConfCache):
    def __init__(self, *args, **kwargs):
        super(ConfigCache, self).__init__(*args, **kwargs)


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
        if isinstance(data, dict) and data.get("key") == key:
            result[pk] = data.get("value")
    missing = [pk for pk in pks if pk not in result]
    if missing:
        # 用户未单独配置时，统一回退到系统级默认值（单次读取）
        system_value = SysConfig.get_value(key, default)
        for pk in missing:
            result[pk] = system_value
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

    def get_default_data(self, key, default_data):
        data = SysConfig.get_data(key, default_data)
        if data and data.get("inherit"):
            return data.get("value")
        return {}

    def delete_db(self, key, **kwargs):
        return super(UserPersonalConfigCache, self).delete_db(key, **self.filter_kwargs)

    def save_db(self, key, value, is_active=None, description=None, **kwargs):
        return super(UserPersonalConfigCache, self).save_db(
            key, value, is_active, description, **self.filter_kwargs, **kwargs
        )

    def set_default_value(self, key, **kwargs):
        return super(UserPersonalConfigCache, self).set_default_value(key, **self.filter_kwargs)


UserConfig = UserPersonalConfigCache
