#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 12/15/2023
"""系统配置缓存：数据访问基类与序列化器。"""

import copy
import json
import re
import time
from typing import Any

from django.template import Context, Template, TemplateSyntaxError
from django.template.base import VariableNode
from rest_framework import serializers

from common.cache.storage import UserSystemConfigCache
from common.contracts import SystemConfig
from common.core.credentials import decrypt_setting_value, encrypt_setting_value
from common.utils import get_logger

logger = get_logger(__name__)


class SystemConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = SystemConfig
        fields = "__all__"


def get_render_context(tmp: str, context: dict[str, Any]) -> str:
    # SysConfig 在 system_conf 子模块定义（继承本模块的 ConfigCacheBase），
    # 函数内惰性导入避免模块级循环依赖
    from .system_conf import SysConfig

    template = Template(tmp)
    for node in template.nodelist:
        if isinstance(node, VariableNode):
            v_key = re.findall(r"<Variable Node: (.*)>", str(node))
            if v_key and v_key[0].isupper():
                context[v_key[0]] = getattr(SysConfig, v_key[0])
    context = Context(context)
    return str(template.render(context))


def build_config_render_context(model: Any) -> dict[str, Any]:
    """{{ KEY }} 渲染上下文：模型全部激活行（key → JSON 字符串值）。

    行值自身含同名渲染键（自引用）时跳过，防止渲染递归；批量回源时整个
    列表只需构建一次。
    """
    context_dict = {}
    for sys_obj_dict in model.objects.filter(is_active=True).values("key", "value").all():
        str_value = json.dumps(sys_obj_dict["value"])  # 将dict转换为json字符串进行匹配
        if re.findall("{{{{.*{}.*}}}}".format(sys_obj_dict["key"]), str_value):
            logger.warning("get same render key. so continue")
            continue
        context_dict[sys_obj_dict["key"]] = str_value
    return context_dict


def render_config_value(value: str, context_dict: dict[str, Any], model: Any) -> Any:
    """渲染单条行值并做 JSON 后处理；解析失败的重试分支按 model 重建上下文。"""
    if value:
        try:
            try:
                value = get_render_context(value, context_dict)
            except TemplateSyntaxError as e:
                res_list = re.findall("Could not parse the remainder: '{{(.*?)}}'", str(e))
                for res in res_list:
                    r_value = render_config_value(f"{{{{{res}}}}}", build_config_render_context(model), model)
                    value = value.replace(f"{{{{{res}}}}}", f"{r_value}")
                value = render_config_value(value, build_config_render_context(model), model)
            except Exception as e:
                logger.warning(f"db config - render failed {e}")
        except Exception as e:
            logger.warning(f"db config - render failed {e}")
    value = value.replace('"(', "").replace(')"', "")  # 支持"({{ h }})"， 为了转换变量，h不能为字符串
    try:
        value = json.loads(value)
    except Exception as e:
        logger.warning(f"db config - json loads failed {e}")
    return value


def serialize_config_rows(serializer: Any, rows: Any) -> list[Any]:
    """配置行统一序列化为缓存数据结构（返回顺序与 rows 一致）。

    与单行 get_value_from_db 同口径：读取侧统一解密（凭据治理，消费方拿
    明文）；行值自引用渲染键时置空 key 视同缺席（防止渲染递归）。
    """
    result = []
    for data in serializer(rows, many=True).data:
        row_key = data.get("key")
        data["value"] = decrypt_setting_value(row_key, data["value"])
        if re.findall(f"{{{{.*{row_key}.*}}}}", json.dumps(data["value"])):
            logger.warning(f"get same render key:{row_key}. so get default value")
            data["key"] = ""
        result.append(data)
    return result


class ConfigCacheBase:
    # 无行 no_row 标记 TTL：只缓存「该 key 无数据行」这一事实（值不落缓存，
    # 每次由调用方默认值现算），信号失效/种子导入/行创建路径都会清理标记，
    # 绕过信号的 ORM 直建行在该窗口内自愈
    ABSENCE_CACHE_TIMEOUT = 60

    # 进程内 L1：标量配置（CSP_MODE / CSP_REPORT_URI / SLOW_REQUEST_THRESHOLD 等）
    # 每请求要读 3+ 次，是固定开销链上的 Redis 往返大头；30s 内改走进程内存。
    # 一致性口径：同进程写路径（post_save/pre_delete 信号、invalid_config_cache、
    # 凭据轮换）同步清理本层；跨进程最迟 L1_TTL 生效——配置变更无需秒级一致，
    # 与 fields.get_search_choices_max_count 的进程内缓存先例同口径。
    L1_TTL = 30
    L1_MAX_ENTRIES = 512
    _L1_STORE: dict[str, tuple[float, Any]] = {}

    # 短 TTL 键（秒 → 覆盖值）：这些键的运维热修正常见做法是**直接改库行**
    # （绕过 post_save 信号 → 缓存不失效），默认 30 天缓存会让改动长期不生效。
    # 给 60s 短 TTL 自愈：读路径最多 1 分钟回归新值，且 L1 同步收紧。
    SHORT_TTL_KEYS = {
        "BACKUP_ALERT_TOKEN": 60,
        "OPS_ALERT_TOKEN": 60,
        "SCIM_TOKEN": 60,
    }

    def _ttl_for(self, key: str) -> int:
        """键级缓存时长：短 TTL 键优先，其余用实例默认（30 天）。"""
        return int(self.SHORT_TTL_KEYS.get(key, self.timeout))

    def __init__(
        self,
        px: str = "system",
        model: Any = SystemConfig,
        cache: Any = UserSystemConfigCache,
        serializer: Any = SystemConfigSerializer,
        timeout: Any = 60 * 60 * 24 * 30,
        filter_kwargs: Any = None,
    ) -> None:
        if filter_kwargs is None:
            filter_kwargs = {}
        self.px = px
        self.model = model
        self.cache = cache
        self.timeout = timeout
        self.serializer = serializer
        self.filter_kwargs = filter_kwargs

    def invalid_config_cache(self, key: str = "*") -> None:
        UserSystemConfigCache(f"{self.px}_{key}").del_many()
        # 同进程 L1 同步清理：否则本进程最长 L1_TTL 内仍读到旧值
        self._l1_clear(self._l1_key(key))

    def _l1_key(self, key: str) -> str:
        """L1 键（进程内命名空间）：与 Redis 缓存类共用 px/key 口径，保持失效对得上。"""
        return f"{self.px}_{key}"

    @classmethod
    def _l1_get(cls, l1_key: Any) -> Any:
        entry = cls._L1_STORE.get(l1_key)
        if not entry:
            return None
        expires, value = entry
        if expires <= time.monotonic():
            cls._L1_STORE.pop(l1_key, None)
            return None
        # 深拷贝返回：Redis 路径每次都是反序列化的新对象，L1 必须保持同一语义
        # （否则调用方就地变更会污染进程内缓存，影响后续所有请求）
        return copy.deepcopy(value)

    @classmethod
    def _l1_set(cls, l1_key: Any, value: Any, ttl: Any = None) -> None:
        if len(cls._L1_STORE) >= cls.L1_MAX_ENTRIES:
            # 容量兜底：用户级配置键理论上无界，超限整体清空（粗粒度但安全）
            cls._L1_STORE.clear()
        # 同样拷贝入池：调用方随后可能变更自己拿到的那份（如 db_data 直接返回）
        cls._L1_STORE[l1_key] = (time.monotonic() + (ttl or cls.L1_TTL), copy.deepcopy(value))

    @classmethod
    def _l1_clear(cls, l1_key: Any) -> None:
        """按 Redis 同款语义清理：``*`` 结尾为前缀匹配，否则精确键。"""
        if isinstance(l1_key, str) and l1_key.endswith("*"):
            head = l1_key[:-1]
            for key in [k for k in cls._L1_STORE if k.startswith(head)]:
                cls._L1_STORE.pop(key, None)
            return
        cls._L1_STORE.pop(l1_key, None)

    def get_render_value(self, value: str) -> Any:
        # 空值不构建渲染上下文：缺席渲染无需触发全表查询（原语义保留）
        return render_config_value(value, build_config_render_context(self.model) if value else {}, self.model)

    def get_value_from_db(self, key: str) -> Any:  # 取得数据是激活的数据，如果数据未激活，则取默认数据
        row = self.model.objects.filter(is_active=True, key=key, **self.filter_kwargs).first()
        if row is None:
            return {}
        data = self.serializer(row).data
        # 凭据治理：敏感键的值内字段解密（读取侧统一收口，消费方拿明文）
        data["value"] = decrypt_setting_value(key, data["value"])
        if re.findall("{{{{.*{}.*}}}}".format(data["key"]), json.dumps(data["value"])):  # 防止渲染出现递归
            logger.warning(f"get same render key:{key}. so get default value")
            data["key"] = ""
        return data

    def get_value_from_db_many(self, keys: Any) -> dict[str, Any]:
        """批量取激活行并序列化：返回 {key: 缓存数据}，缺席的 key 不在结果中。

        与 get_value_from_db 同口径（只取激活行、读取侧解密、自引用渲染键
        视同缺席）；差异仅在一次 key__in 查询 + 一次批量序列化。
        """
        key_list = list(keys)
        if not key_list:
            return {}
        rows = list(self.model.objects.filter(is_active=True, key__in=key_list, **self.filter_kwargs))
        return {row.key: data for row, data in zip(rows, serialize_config_rows(self.serializer, rows), strict=True)}

    def _resolve_cached_data(self, key: str, cache_data: Any, default_data: Any, ignore_access: Any) -> Any:
        """缓存数据 → 生效数据；视同未命中（需回源 DB）时返回 None。

        与 get_data 的命中判定同口径：键不匹配 / access 拦下时回源，
        no_row 缺席标记直接按缺席语义返回（不回源）。
        """
        if not (isinstance(cache_data, dict) and cache_data.get("key", "") == key):
            return None
        if cache_data.get("no_row"):
            return self._absence_value(key, default_data)
        if ignore_access or cache_data.get("access"):
            return cache_data
        return None

    def get_values(self, keys: Any, default_data: Any = None, ignore_access: bool = True) -> Any:
        """批量读取多个 key 的生效值，返回 {key: value}。

        语义与逐个 get_value 一致（L1/缓存命中、access 过滤、缺席默认值、
        解密与渲染），仅合并往返：L1 命中后剩余 key 一次 Redis get_many，
        仍未命中的 key 一次 key__in 查询回源（渲染上下文只建一次）。
        只读不回填缓存——值与缺席标记的写路径仍由单读负责，批量读取不改变
        缓存状态的演进。
        """
        from django.core.cache import cache as django_cache

        key_list = list(dict.fromkeys(keys))
        if not key_list:
            return {}
        resolved, pending = {}, []
        for key in key_list:
            cache_data = self._l1_get(self._l1_key(key))
            if cache_data is not None:
                data = self._resolve_cached_data(key, cache_data, default_data, ignore_access)
                if data is not None:
                    resolved[key] = data
                    continue
            pending.append(key)
        if pending:
            slot_keys = {key: self.cache(f"{self.px}_{key}").cache_key for key in pending}
            try:
                cached = django_cache.get_many(set(slot_keys.values()))
            except Exception:  # noqa: BLE001 Redis 不可用（与单读同口径）：降级读库
                logger.warning("config cache read failed, fallback to db", exc_info=True)
                cached = {}
            still_pending = []
            for key in pending:
                data = self._resolve_cached_data(key, cached.get(slot_keys[key]), default_data, ignore_access)
                if data is not None:
                    resolved[key] = data
                else:
                    still_pending.append(key)
            if still_pending:
                db_map = self.get_value_from_db_many(still_pending)
                context = build_config_render_context(self.model) if db_map else {}
                for key in still_pending:
                    db_data = db_map.get(key)
                    if db_data is None or db_data.get("key") != key:
                        # 无行 / 自引用渲染键：缺席语义（默认值现算，不落缓存）
                        resolved[key] = self._absence_value(key, default_data)
                        continue
                    db_data["value"] = render_config_value(json.dumps(db_data["value"]), context, self.model)
                    resolved[key] = db_data if (ignore_access or db_data.get("access")) else {}
        # 与 get_value 的返回整形一致：数据为空（缺席/拦下）原样返回空 {}
        return {key: (data.get("value") if data else data) for key, data in resolved.items()}

    def get_default_data(self, key: str, default_data: Any) -> Any:
        if default_data is None:
            default_data = {}
        return default_data

    def get_value(self, key: str, default_data: Any = None, ignore_access: bool = True) -> Any:
        data = self.get_data(key, default_data, ignore_access)
        if data:
            return data.get("value")
        return data

    def get_data(self, key: str, default_data: Any = None, ignore_access: bool = True) -> Any:
        cache = self.cache(f"{self.px}_{key}")
        l1_key = self._l1_key(key)
        ttl = self._ttl_for(key)
        cache_data = self._l1_get(l1_key)
        if cache_data is None:
            try:
                cache_data = cache.get_storage_cache()
            except Exception:  # noqa: BLE001 Redis 不可用（故障演练 2029-10）：降级读库，不阻断请求
                logger.warning("config cache read failed, fallback to db", exc_info=True)
                cache_data = None
            if cache_data is not None:
                # 写穿 L1：Redis 已有值（含 access=False / no_row 标记）原样缓存，
                # 访问控制在 L1 之外判定，语义与直读 Redis 一致
                self._l1_set(l1_key, cache_data, ttl=min(ttl, self.L1_TTL))
        if cache_data is not None and cache_data.get("key", "") == key:
            if cache_data.get("no_row"):
                return self._absence_value(key, default_data)
            if ignore_access or cache_data.get("access"):
                return cache_data
        db_data = self.get_value_from_db(key)
        if db_data.get("key") != key:
            # 无行：缓存 no_row 标记（短 TTL）——既不把空值/默认值固化进缓存
            # （default_data 由调用方每次给定），也避免无行键每次读都查库
            try:
                cache.set_storage_cache({"key": key, "no_row": True}, timeout=self.ABSENCE_CACHE_TIMEOUT)
            except Exception:  # noqa: BLE001 写缓存失败不影响本次读数
                logger.warning("config cache write failed, skipped", exc_info=True)
            self._l1_set(l1_key, {"key": key, "no_row": True})
            return self._absence_value(key, default_data)
        db_data["value"] = self.get_render_value(json.dumps(db_data["value"]))
        try:
            cache.set_storage_cache(db_data, timeout=ttl)
        except Exception:  # noqa: BLE001 写缓存失败不影响本次读数
            logger.warning("config cache write failed, skipped", exc_info=True)
        self._l1_set(l1_key, db_data, ttl=min(ttl, self.L1_TTL))
        if ignore_access or db_data.get("access"):
            return db_data
        return {}

    def _absence_value(self, key: str, default_data: Any) -> Any:
        """无行（系统级/用户级通用）时的返回：调用方默认值的纯 JSON 拷贝。

        不做模板渲染——无行场景下 {{ KEY }} 引用的源行同样不存在，渲染只会
        白白多一次全表查询；default_data 为 None 时返回空 {}（缺席语义自担）。
        """
        if default_data is None:
            return {}
        return {"key": key, "value": json.loads(json.dumps(default_data)), "access": True}

    def save_db(self, key: str, value: Any, is_active: Any, description: Any, **kwargs: Any) -> Any:
        # 凭据治理：敏感键的值内字段加密（写入侧统一收口，幂等）
        defaults = {"value": encrypt_setting_value(key, value)}
        if is_active is not None:
            defaults["is_active"] = is_active
        if description is not None:
            defaults["description"] = description
        return self.model.objects.update_or_create(key=key, defaults=defaults, **kwargs)

    def delete_db(self, key: str, **kwargs: Any) -> Any:
        return self.model.objects.filter(key=key, **kwargs).delete()

    def set_value(self, key: str, value: Any, is_active: Any = None, description: Any = None, **kwargs: Any) -> Any:
        obj = self.save_db(key, value, is_active, description, **kwargs)
        self.cache(f"{self.px}_{key}").del_storage_cache()
        return obj

    def set_default_value(self, key: str, **kwargs: Any) -> Any:
        return self.set_value(key, self.get_value(key, None), **kwargs)

    def del_value(self, key: str, **kwargs: Any) -> None:
        self.delete_db(key, **kwargs)
        self.cache(f"{self.px}_{key}").del_storage_cache()

    def __getattribute__(self, name: str) -> Any:
        if name == "shape":
            return ""
        try:
            return object.__getattribute__(self, name)
        except AttributeError as e:
            # 属性访问即"读同名配置"是本类的设计；此处只兜底 AttributeError，
            # 避免把 property 内部的真实异常（TypeError/KeyError 等）也吞成"读配置"
            logger.debug(f"__getattribute__ fallback to config. name:{name} error:{e}")
            return self.get_value(name)
