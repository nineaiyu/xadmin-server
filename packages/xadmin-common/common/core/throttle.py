#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : throttle
# author : ly_13
# date : 6/2/2023


from typing import Any

from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle, UserRateThrottle

from common.utils import get_logger

logger = get_logger(__name__)


def allow_by_identity(ident: Any, scope: str, limit: int, window_seconds: int = 60) -> bool:
    """固定窗口限流（非 DRF 场景通用入口，如原生视图 / WebSocket 消费者）：True = 放行。

    计数落在 Redis（`cache.add` 建窗 + `incr` 计数，单键原子自增，窗口首请求占位），
    缓存故障时放行（fail-open）：限流是加固，不应因缓存抖动阻断业务。
    """
    from django.core.cache import cache

    key = f"throttle_{scope}_{ident}"
    try:
        if cache.add(key, 1, window_seconds):
            return True
        return int(cache.incr(key)) <= int(limit)
    except Exception:  # noqa: BLE001 缓存故障不阻断业务（与 PatThrottle 容错口径一致）
        return True


def allow_by_ip(request: Any, scope: str, limit: int, window_seconds: int = 60) -> bool:
    """IP 固定窗口限流（非 DRF 视图用，如 Django 原生验证码端点）：True = 放行。

    验证码出题 / 音频每次都会写挑战缓存并消耗 Pillow / TTS 资源，匿名可刷即等于可灌库；
    DRF 的限流类只能在 DRF 视图上挂载，此函数提供同等语义的独立入口。
    """
    from common.utils.request import get_request_ip

    ident = get_request_ip(request) or "unknown"
    return allow_by_identity(ident, scope=scope, limit=limit, window_seconds=window_seconds)


class RegisterThrottle(AnonRateThrottle):
    scope = "register"


class ResetPasswordThrottle(AnonRateThrottle):
    scope = "reset_password"


class LoginThrottle(AnonRateThrottle):
    scope = "login"


class IpScopedThrottle(SimpleRateThrottle):
    """匿名端点按来源 IP 限流基类：IP 口径走 ``get_request_ip``（防 XFF 伪造，
    与 IP 封禁/审计同源）；全局 AnonRateThrottle 之外的单列收紧档用此基类。"""

    def get_cache_key(self, request: Any, view: Any) -> Any:
        from common.utils.request import get_request_ip

        return self.cache_format % {"scope": self.scope, "ident": get_request_ip(request)}


class ClientScopedThrottle(SimpleRateThrottle):
    """匿名凭证端点按 client_id 限流基类：凭据即身份，计数维度跟 client 走。

    请求未带 client_id 时回退来源 IP（防随机 id 洗桶绕过 client 维度）；键内
    以 ``client_`` / ``ip_`` 前缀区分两个维度，避免互相撞键。
    """

    def get_cache_key(self, request: Any, view: Any) -> Any:
        from common.utils.request import get_request_ip

        data = getattr(request, "data", None)
        # JSON 解析产物与 QueryDict 均为 dict 子类；其余形态（None 等）直接走 IP 维度
        client_id = str(data.get("client_id") or "").strip() if isinstance(data, dict) else ""
        if client_id:
            ident = f"client_{client_id}"
        else:
            ident = f"ip_{get_request_ip(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class OpenClientThrottle(ClientScopedThrottle):
    """开放平台 client-credentials 换发限流：按 client 维度防
    client_secret 在线爆破与换发风暴（换发即轮换，频繁调用等于凭证写放大）。"""

    scope = "open_client"


class OAuthClientThrottle(ClientScopedThrottle):
    """OAuth token/revoke 限流：授权码/刷新/撤销按 client 维度收敛；
    多用户共用同一应用的后端调用，速率须覆盖正常登录高峰。"""

    scope = "oauth_client"


class VerifyCodeThrottle(IpScopedThrottle):
    """发送验证码限流：IP 维度收敛短信/邮件轰炸与 Redis 写放大；
    按目标计数的锁定由 SendVerifyCodeBlockUtil 兜底（互补维度）。"""

    scope = "verify_code"


class TempTokenThrottle(IpScopedThrottle):
    """临时令牌限流：每次调用强制生成新缓存令牌（Redis 写放大面）
    收紧到低于全局匿名档。"""

    scope = "temp_token"


class CaptchaChallengeThrottle(IpScopedThrottle):
    """交互式验证码出题限流：每次请求生成底图并写缓存，按 IP 收敛刷题。"""

    scope = "captcha_challenge"


class CaptchaVerifyThrottle(IpScopedThrottle):
    """交互式验证码校验限流：按 IP 收敛坐标枚举与缓存读写风暴。"""

    scope = "captcha_verify"


class CaptchaAudioThrottle(IpScopedThrottle):
    """图片验证码音频限流：每次请求调用 flite/sox 子进程，成本远高于出题。

    同一挑战可反复拉取音频（不像校验会消费挑战），因此必须单独收紧，
    否则单个挑战即可把音频端点当作 TTS 算力放大器。
    """

    scope = "captcha_audio"


class UploadThrottle(UserRateThrottle):
    """上传速率限制"""

    scope = "upload"


class PasswordVerifyThrottle(UserRateThrottle):
    """口令核验（个人口令校验入口，如锁屏解锁）按用户维度限流。

    端点本身要求登录态，防的是「已登录会话被用于在线爆破本人口令」这一类
    横向利用（会话泄露 / XSS），因此计数维度取用户而非 IP：办公网等共享出口
    下按 IP 计数会互相牵连。"""

    scope = "password_verify"


class Download1Throttle(UserRateThrottle):
    """下载速率限制"""

    scope = "download1"


class Download2Throttle(UserRateThrottle):
    """下载速率限制"""

    scope = "download2"


class AiChatThrottle(UserRateThrottle):
    """AI 对话类端点限流（文档问答 / NL 查数 / 受限动作 / 聊天室 AI，含流式）。

    LLM 代理调用是全站外部成本最高的入口：应用层节流（注入告警去重）与用量
    配额（日调用 / 日 token / 并发流式）都不限制「短时间突发」，此按用户维度
    的 DRF 限流补齐这一层（超限 429，不影响配额统计口径）。
    """

    scope = "ai_chat"


class AiAdminThrottle(UserRateThrottle):
    """AI 管理类重操作限流（连接测试 / 档案探测 / 知识库同步 / 向量构建）。

    这些动作会外呼供应商或全量扫描知识库，管理端误操作/脚本重试即可放大成本。
    """

    scope = "ai_admin"


class AiThrottleMixin:
    """AI 端点限流挂载：按 action 追加限流类（不替换默认链，保留匿名/PAT 限流）。

    子类声明命中集合：``ai_chat_actions`` / ``ai_admin_actions`` 为 action 名元组；
    ``ai_chat_all`` / ``ai_admin_all`` 为 True 时该类全部请求命中（聊天室 AI 视图、
    MCP 端点等无 DRF action 语义的场景）。
    """

    ai_chat_actions: tuple[Any, ...] = ()
    ai_admin_actions: tuple[Any, ...] = ()
    ai_chat_all: bool = False
    ai_admin_all: bool = False

    def get_throttles(self) -> Any:
        throttles = list(super().get_throttles())  # type: ignore[misc]  # 宿主 ViewSet 提供基类实现（mixin 模式）
        action = getattr(self, "action", None)
        if self.ai_chat_all or (action and action in self.ai_chat_actions):
            throttles.append(AiChatThrottle())
        if self.ai_admin_all or (action and action in self.ai_admin_actions):
            throttles.append(AiAdminThrottle())
        return throttles


class ExportImportThrottle(UserRateThrottle):
    """导出/导入重 IO 端点限流：按用户维度收敛导出/导入风暴。

    覆盖面（经 ExportImportThrottleMixin 按声明联合命中）：同步导出
    export-data、异步提交 export-async、导入三段 import-headers/-validate/-async、
    下载中心产物 download。并发上限另有 EXPORT_ASYNC_MAX_RUNNING 兜底，本档补
    「短时间突发提交/下载」一层；k6 压测与 E2E 档在各自 settings 放开。
    """

    scope = "export_import"


class DatasetQueryThrottle(UserRateThrottle):
    """数据集执行/聚合限流：大表聚合与明细拉取是重 DB 操作，按用户维度收敛
    高频刷新（看板卡片轮询 / 脚本重放造成的聚合风暴）。"""

    scope = "dataset_query"


class DatasetQueryThrottleMixin:
    """数据集执行/聚合端点限流挂载（ExportImportThrottleMixin 同款模式）。

    声明集合 ``dataset_query_actions`` 沿 MRO 取并集；数据集视图按需挂载。
    """

    dataset_query_actions: tuple[str, ...] = ()

    def get_throttles(self) -> Any:
        throttles = list(super().get_throttles())  # type: ignore[misc]  # 宿主 ViewSet 提供基类实现（mixin 模式）
        action = getattr(self, "action", None)
        if action:
            declared: set[Any] = set()
            for klass in type(self).__mro__:
                declared.update(getattr(klass, "dataset_query_actions", ()) or ())
            if action in declared:
                throttles.append(DatasetQueryThrottle())
        return throttles


class ExportImportThrottleMixin:
    """导出/导入端点限流挂载（AiThrottleMixin 同款模式）。

    声明集合 ``export_import_actions`` 沿 MRO 取**并集**：导出/导入 Action mixin
    （OnlyExportDataAction / ImportAsyncAction）与下载 mixin
    （RecordFileDownloadMixin）各自声明、组合视图自动合并——与
    ``SENSITIVE_GET_ACTIONS`` 同口径，避免覆盖式属性互相屏蔽。
    """

    export_import_actions: tuple[str, ...] = ()

    def get_throttles(self) -> Any:
        throttles = list(super().get_throttles())  # type: ignore[misc]  # 宿主 ViewSet 提供基类实现（mixin 模式）
        action = getattr(self, "action", None)
        if action:
            declared: set[Any] = set()
            for klass in type(self).__mro__:
                declared.update(getattr(klass, "export_import_actions", ()) or ())
            if action in declared:
                throttles.append(ExportImportThrottle())
        return throttles


class PatThrottle(SimpleRateThrottle):
    """PAT 凭证级限流：按 token_hash 计数（PAT_RATE_LIMIT，空/0 = 不限）。

    全局挂载（DEFAULT_THROTTLE_CLASSES）：非 PAT 请求 get_cache_key 返回 None
    直接放行，仅对 PAT 认证生效；速率按认证用户动态取值——个人行优先，
    未设置/未启用继承时回退系统级（区别于静态 THROTTLE_RATES）。
    """

    scope = "pat"

    def get_rate(self, request: Any = None) -> Any:
        # 覆盖默认实现：速率来自 SysConfig/UserConfig 动态配置而非静态 THROTTLE_RATES；
        # 空 / "0"（数字 0 同）= 不限（直接传 parse_rate 会因缺单位 ValueError）
        from common.core.config import SysConfig, UserConfig

        if request is not None:
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated:
                # 用户级值已内含继承链（无个人行时即系统值），非空即权威，
                # 个人行 "0"（不限）不会被系统级值覆盖
                limit = UserConfig(user).PAT_RATE_LIMIT
                if isinstance(limit, str) and limit.strip():
                    limit = limit.strip()
                    return None if limit == "0" else limit
        limit = str(SysConfig.PAT_RATE_LIMIT or "").strip()
        return None if not limit or limit == "0" else limit

    def allow_request(self, request: Any, view: Any) -> Any:
        # 实例化早于认证（拿不到 request.user），PAT 请求在此按认证用户重取速率；
        # 非 PAT 请求维持实例化时的系统级判定，不额外读用户配置。
        # rate 变更须同步重算 num_requests/duration（DRF 在实例化期解析一次）
        if self.get_cache_key(request, view) is not None:
            self.rate = self.get_rate(request)
            try:
                self.num_requests, self.duration = self.parse_rate(self.rate)
            except (ValueError, KeyError, IndexError):
                # 配置写坏（如 "abc" / "60" 缺单位）不能让 PAT 请求全线 500：
                # 告警并按「不限」放行，等待配置修正（写入侧校验是主防线，此处兜底）
                logger.warning(f"Invalid PAT rate limit config: {self.rate!r}, treated as unlimited")
                self.rate = None
        if self.rate is None:
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request: Any, view: Any) -> Any:
        from django.apps import apps

        pat_model = apps.get_model("identity", "PersonalAccessToken")
        auth = getattr(request, "auth", None)
        if auth is not None and isinstance(auth, pat_model):
            return self.cache_format % {"scope": self.scope, "ident": auth.token_hash}
        return None
