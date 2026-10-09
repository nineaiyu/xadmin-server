#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : config
# author : ly_13
# date : 12/15/2023
"""系统配置缓存：个人级配置（继承系统级 + 个人行覆盖）。"""

import json
from typing import Any

from django.core.exceptions import ValidationError
from rest_framework import serializers

from common.cache.storage import UserSystemConfigCache
from common.contracts import UserPersonalConfig
from common.utils import get_logger

from .base import build_config_render_context, render_config_value, serialize_config_rows
from .system_conf import ConfigCache, SysConfig

logger = get_logger(__name__)


def _normalized_owner_ids(owner_ids: Any) -> dict[Any, Any]:
    """把入参 owner 标识（用户 pk / ``str(pk)``）归一为 ORM 口径（UUID）。

    两种调用形态并存：``UserConfig(user_obj)`` 与 ``UserConfig(str_pk)``，而 owner_id
    列与 DB 查询返回值恒为 UUID。用入参原值直接与行匹配/做字典键会静默漏掉个人行
    （表现为批量读取回退系统值，而单读路径正常）。返回 {入参原值: 归一值}，非法值跳过。
    """
    pk_field = UserPersonalConfig._meta.pk
    mapping: dict[Any, Any] = {}
    for raw in owner_ids:
        try:
            mapping[raw] = pk_field.to_python(raw)
        except (TypeError, ValueError, ValidationError):
            # 非 UUID 形态的 owner 标识：保留原值（与归一前口径一致，交给 ORM /
            # 缓存键按既有方式处理），只告警不丢弃——丢弃会让该 owner 整批读不到值
            logger.warning(f"owner id is not normalized in batch user config: {raw!r}")
            mapping[raw] = raw
    return mapping


def batch_user_config(user_pks: Any, key: str, default: Any = None) -> Any:
    """批量读取多个用户的同一配置项，返回 {user_pk: value}。

    逐用户 UserConfig(pk).key 会产生 N 次缓存读 + N 次 DB 回源；这里一次 get_many
    批量取缓存，缺失项只回源一次系统级默认值。系统公告/性能告警等全量推送场景
    由 N 次降为常数次。
    """
    from django.core.cache import cache as django_cache

    pks = list(dict.fromkeys(user_pks))
    if not pks:
        return {}
    owner_ids = _normalized_owner_ids(pks)
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
            # DB 行的 owner_id 是 UUID，入参可能是 str(pk)：按归一键取值，否则静默丢个人行
            result[pk] = personal.get(owner_ids.get(pk), system_value)
    return result


class UserConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPersonalConfig
        fields = "__all__"


def batch_user_config_values(owner_key_pairs: Any, default_data: Any = None, ignore_access: bool = True) -> Any:
    """批量读取多个 (owner_id, key) 的个人级生效值，返回 {(owner_id, key): value}。

    逐对 UserConfig(owner).get_value(key) 是 N 次缓存往返 + miss 逐行回源；
    这里个人槽一次 get_many，未命中的 (owner_id, key) 一次 key__in 查询回源
    个人行（解密 + 渲染上下文只建一次），仍无个人行的 key 一次系统级批量
    读取兜底。缺席语义与逐对读取一致：无个人行 → 系统生效值，两级皆缺席
    → {}。个人槽只读不回填——值/缺席标记的写路径仍由单读负责，批量读取
    不改变缓存状态的演进（旧版 inherit 槽的清理同理交给单读）。
    """
    from django.core.cache import cache as django_cache

    pairs = list(dict.fromkeys(owner_key_pairs))
    if not pairs:
        return {}
    owner_ids = _normalized_owner_ids({owner_id for owner_id, _ in pairs})
    result, pending = {}, []
    slot_keys = {(owner_id, key): UserSystemConfigCache(f"user_{owner_id}_{key}").cache_key for owner_id, key in pairs}
    try:
        cached = django_cache.get_many(set(slot_keys.values()))
    except Exception:  # noqa: BLE001 Redis 不可用（与单读同口径）：降级读库
        logger.warning("config cache read failed, fallback to db", exc_info=True)
        cached = {}
    for pair, slot_key in slot_keys.items():
        owner_id, key = pair
        data = cached.get(slot_key)
        # 缺席标记 / 旧版继承槽（inherit）/ 键不匹配：视同无个人行，回源或系统级兜底
        if (
            isinstance(data, dict)
            and data.get("key", "") == key
            and not data.get("no_row")
            and "inherit" not in data
            and (ignore_access or data.get("access"))
        ):
            result[pair] = data.get("value")
        else:
            pending.append(pair)
    if not pending:
        return result
    rows = list(
        UserPersonalConfig.objects.filter(
            is_active=True,
            owner_id__in={owner_ids[owner_id] for owner_id, _ in pending if owner_id in owner_ids},
            key__in={key for _, key in pending},
        )
    )
    # DB 行的 (owner_id, key) 恒为 UUID 口径，入参 pair 可能是 str(pk)：按归一键反查入参 pair，
    # 否则行永远匹配不上入参，个人值被静默忽略
    pair_of = {(owner_ids[owner_id], key): (owner_id, key) for owner_id, key in pending if owner_id in owner_ids}
    context = build_config_render_context(UserPersonalConfig) if rows else {}
    system_pairs, found_pairs = [], set()
    for row, data in zip(rows, serialize_config_rows(UserConfigSerializer, rows), strict=True):
        matched_pair = pair_of.get((row.owner_id, row.key))
        if matched_pair is None:  # IN 条件与返回行不匹配（防御）：跳过而不是让整批读取失败
            continue
        if data.get("key") != row.key:  # 自引用渲染键视同缺席（防渲染递归）
            system_pairs.append(matched_pair)
            continue
        data["value"] = render_config_value(json.dumps(data["value"]), context, UserPersonalConfig)
        result[matched_pair] = data["value"] if (ignore_access or data.get("access")) else {}
        found_pairs.add(matched_pair)
    # 未回源到行的 pair（无行 / 未激活 / 自引用守卫）：回退系统级生效值。
    # 与单读一致：系统级读取不透传 ignore_access（缺席继承始终可读）
    system_pairs.extend(pair for pair in pending if pair not in found_pairs)
    fallback_keys = {key for _, key in system_pairs}
    sys_values = SysConfig.get_values(fallback_keys, default_data) if fallback_keys else {}
    for pair in system_pairs:
        result.setdefault(pair, sys_values.get(pair[1], {}))
    return result


class UserPersonalConfigCache(ConfigCache):
    def __init__(self, user_obj: Any) -> None:
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

    def _absence_value(self, key: str, default_data: Any) -> Any:
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

    def get_data(self, key: str, default_data: Any = None, ignore_access: bool = True) -> Any:
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

    def get_values(self, keys: Any, default_data: Any = None, ignore_access: bool = True) -> Any:
        """用户级批量读取：个人行优先，缺席继承系统生效值（口径同 get_value）。

        委托 batch_user_config_values 走跨用户批量实现（单用户是其特例），
        返回按 key 索引的 {key: value}，与系统级 get_values 对齐。
        """
        owner_id = self.user_obj if isinstance(self.user_obj, (str, int)) else self.user_obj.pk
        paired = batch_user_config_values([(owner_id, key) for key in keys], default_data, ignore_access)
        return {key: paired[(owner_id, key)] for key in keys}

    def delete_db(self, key: str, **kwargs: Any) -> Any:
        return super().delete_db(key, **self.filter_kwargs)

    def save_db(self, key: str, value: Any, is_active: Any = None, description: Any = None, **kwargs: Any) -> Any:
        return super().save_db(key, value, is_active, description, **self.filter_kwargs, **kwargs)

    def set_default_value(self, key: str, **kwargs: Any) -> Any:
        return super().set_default_value(key, **self.filter_kwargs)


UserConfig = UserPersonalConfigCache


def get_personal_config_data(user_obj: Any, key: str) -> Any:
    """返回用户真实个人行的完整缓存数据（无个人行返回 None）。

    缺席标记/系统生效值回退（no_row/system_fallback）均视为「未个性化」，
    返回 None；供配额「个人行优先、缺席继承系统级」语义使用。
    """
    data = UserConfig(user_obj).get_data(key, None)
    is_personal = isinstance(data, dict) and data.get("key") == key
    if is_personal and not data.get("no_row") and not data.get("system_fallback"):
        return data
    return None


def get_personal_int_config(user_obj: Any, key: str, system_value: Any) -> Any:
    """个人级 int 配置读取：真实个人行优先（int 类型才生效），否则回退系统级。"""
    data = get_personal_config_data(user_obj, key)
    if data is not None and isinstance(data.get("value"), int):
        return data["value"]
    return system_value
