#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""操作日志落库辅助：大字段截断 / 脱敏 / 身份解析 / 信息装配（自 middleware.py 拆分，行为不变）。"""

import json
import time
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.http import StreamingHttpResponse
from rest_framework.utils import encoders

from common.contracts import OperationLog, PersonalAccessToken
from common.core.auth.credentials import SENSITIVE_KEY_PATTERN, SENSITIVE_SETTING_KEYS
from common.core.auth.sensitive import SENSITIVE_FIELDS as SENSITIVE_FIELDS
from common.core.config import SysConfig
from common.core.response import API_SUCCESS_CODE
from common.core.url_inventory import get_doc_first_line
from common.utils import get_logger
from common.utils.request import (
    get_browser,
    get_os,
    get_request_user,
)

logger = get_logger(__name__)


# 日志大字段截断上限（系统配置 OPERATION_LOG_FIELD_MAX 的默认值），
# 避免大请求体/大响应整包入库；运行期取值见 _log_field_limit()
MAX_LOG_FIELD = 4096
# 操作日志脱敏字段清单单一事实源在 common.core.auth.sensitive（拆出，顶部导入
# SENSITIVE_FIELDS 随之再导出保持既有导入路径不变）


def _log_field_limit() -> Any:
    """大字段（请求体/响应/变更）截断上限：系统配置 OPERATION_LOG_FIELD_MAX。

    默认 4096；0 = 不落大字段内容（只保留状态码等元数据）。配置异常时回落默认值，
    避免坏配置把响应阶段的日志组装打成 500。
    """
    try:
        return max(int(SysConfig.OPERATION_LOG_FIELD_MAX), 0)
    except (TypeError, ValueError):
        return MAX_LOG_FIELD


# module 列的防御性截断：视图 docstring/模型标签超长时按字段上限截断，
# 避免写日志失败放大成整个请求 500（mfa confirm 曾因此全挂）
OPERATION_LOG_MODULE_MAX = OperationLog._meta.get_field("module").max_length
# 健康检查端点：探针高频请求，中间件请求/响应阶段直接跳过（不落操作日志）
HEALTH_CHECK_PATH = "/api/common/api/health"


# SystemConfig 键值对写入体（POST/PATCH /api/system/config）里承载凭据本体的
# 兄弟字段：body 形如 {"key": "SCIM_TOKEN", "value": "<明文>"}——"value"/"detail"
# 不在键名精确匹配名单，按「key 值命中整值加密配置键」的值语义补脱敏（G18）
PAIR_VALUE_FIELDS = ("value", "detail")
# 整值加密的 SystemConfig 键（小写）：字段级键（如 OAUTH_PROVIDERS.client_secret）
# 已由键名精确匹配覆盖，此处只收整值键（与 credentials.SENSITIVE_SETTING_KEYS 同源）
_WHOLE_VALUE_CONFIG_KEYS = frozenset(str(key).lower() for key, fields in SENSITIVE_SETTING_KEYS.items() if not fields)

# 请求级「值是凭据本体」的字段标记：键在 URL/上游行、值在 body 的形态
# （如 PATCH /api/system/config/{pk} → {"value": "<明文>"}）无法按键名判定，
# 由能判定语义的序列化器/视图登记，脱敏按值语义收口。
VALUE_FIELDS_REQUEST_ATTR = "operation_log_sensitive_value_fields"


def mark_sensitive_value_fields(request: Any, fields: Any) -> None:
    """登记本请求中「值是凭据本体」的字段名（操作日志脱敏用）。

    ``request`` 可传 DRF ``Request``（自动解引用到底层 HttpRequest）或原生
    HttpRequest；无请求上下文（离线序列化 / 导入导出）时静默跳过。
    """
    if request is None:
        return
    raw = getattr(request, "_request", request)
    current = set(getattr(raw, VALUE_FIELDS_REQUEST_ATTR, ()) or ())
    current.update(str(field) for field in fields)
    setattr(raw, VALUE_FIELDS_REQUEST_ATTR, current)


def request_value_fields(request: Any) -> tuple[str, ...]:
    """读取本请求登记的「值语义敏感字段」（无登记返回空元组）。"""
    return tuple(sorted(getattr(request, VALUE_FIELDS_REQUEST_ATTR, ()) or ()))


def desensitize_payload(value: Any, sensitive_value_fields: tuple[str, ...] = ()) -> Any:
    """递归脱敏：dict 按键名掩码、list 逐项处理，其余（标量 / 非容器）原样返回。

    请求体（含嵌套 payload、嵌套 list）与**响应体**共用同一口径——登录响应的
    ``access`` / ``refresh`` 与临时令牌响应的 ``token`` 都在此收敛，不再明文
    进操作日志的 ``body`` / ``response_result`` 与 DEBUG/慢请求日志。
    掩码保留长度信息（与原值等长），便于排障时识别字段是否为空。

    三类命中口径（G18：脱敏不能只靠键名精确匹配）：

    1. 键名精确命中 ``SENSITIVE_FIELDS``（原有口径）；
    2. 键名命中敏感模式（password / secret / token / api_key…，与
       credentials.SENSITIVE_KEY_PATTERN 同源）且值为**非空字符串**——SCIM_TOKEN
       等凭据以自身名为 JSON 键出现时同样收敛；仅收字符串值，max_tokens 等
       命中模式的数值计数器不受影响；
    3. 键值对形态：dict 的 ``key`` 值命中整值加密 SystemConfig 键时，兄弟
       ``value`` / ``detail`` 视为凭据本体一并掩码（POST /api/system/config
       写入 SCIM_TOKEN 等标量凭据是主场景）；
    4. 请求级登记字段（``sensitive_value_fields``）：PATCH /api/system/config/{pk}
       的请求体只有 ``{"value": "<明文>"}``，敏感键在上游行而不在 body 里，
       由序列化器按实例键判定后经 :func:`mark_sensitive_value_fields` 登记。
    """
    if isinstance(value, dict):
        pair_key = str(value.get("key") or "").strip().lower()
        pair_sensitive = pair_key in _WHOLE_VALUE_CONFIG_KEYS
        masked: dict[str, Any] = {}
        for key, item in value.items():
            key_name = str(key)
            hit = (
                (key in SENSITIVE_FIELDS and item)
                or (isinstance(item, str) and item and bool(SENSITIVE_KEY_PATTERN.search(key_name)))
                or (pair_sensitive and key in PAIR_VALUE_FIELDS and item)
                or (key_name in sensitive_value_fields and item)
            )
            masked[key] = "*" * len(str(item)) if hit else desensitize_payload(item, sensitive_value_fields)
        return masked
    if isinstance(value, (list, tuple)):
        return [desensitize_payload(item, sensitive_value_fields) for item in value]
    return value


def desensitize_body(body: Any, sensitive_value_fields: tuple[str, ...] = ()) -> Any:
    """对请求体中的敏感字段做掩码处理（递归入口，兼容既有调用点）。"""
    return desensitize_payload(body, sensitive_value_fields)


def sensitive_get_actions(view_cls: Any) -> frozenset[Any]:
    """视图类声明的「敏感 GET action」集合（敏感读取审计）。

    ``API_LOG_METHODS`` 默认不含 GET（列表/详情读请求全部落库即日志洪水），
    导出/下载等敏感读取由视图侧按 action 白名单单列声明：类（或其任一祖先
    mixin）定义 ``SENSITIVE_GET_ACTIONS = (action 名, …)``，此处沿 MRO 取并集
    （多 mixin 各自声明、组合视图自动合并，覆盖式属性会互相屏蔽故不用单值）。
    命中与否最终仍受 ``API_LOG_IGNORE``（模型 / 路径维度）与 ``API_LOG_ENABLE``
    管辖。
    """
    actions: set[Any] = set()
    for klass in getattr(view_cls, "__mro__", ()):
        actions.update(getattr(klass, "SENSITIVE_GET_ACTIONS", ()) or ())
    return frozenset(actions)


def log_body_preview(payload: Any, sensitive_value_fields: tuple[str, ...] = ()) -> str:
    """日志正文预览：脱敏 + 按大字段上限截断（DEBUG / 慢请求日志共用）。

    ``OPERATION_LOG_FIELD_MAX=0``（不落大字段）时同样不落正文——配置意图对
    运行日志一致；脱敏先于截断，避免截断把敏感串留在前缀里。
    """
    limit = _log_field_limit()
    text = json.dumps(desensitize_payload(payload, sensitive_value_fields), cls=encoders.JSONEncoder, default=str)
    return text[:limit] if limit else ""


def insert_operation_log(info: Any) -> Any:
    """响应阶段一次性落库（单条 INSERT，无占位行、无 UPDATE 回填）。

    事务边界结论（旧「占位行 + on_commit UPDATE」方案的收口，代码实证 +
    Django 源码核对）：``BaseHandler._get_response`` 先执行 view middleware
    （``process_view``）再 ``make_view_atomic`` 包视图，``process_view`` 处于
    ATOMIC_REQUESTS 事务**之外**——在此写库必然走 autocommit 立即提交，与请求
    事务既不同生也不共死（包一层 ``atomic`` 也没有外层事务可加入）；响应阶段
    （``process_response``）同样在事务外，``transaction.on_commit`` 在 autocommit
    下**立即执行**而非「提交后」。旧方案因此有三处与注释不符的实际行为：请求
    事务回滚带不走占位行、on_commit 并非延迟到提交后、并发读方（审计列表）可
    见半成品行；且本项目统一异常处理器把视图内未预期异常兜成 500 响应（不向
    外传播），「视图异常逃逸成孤儿行」在 DRF 面上不会发生，依赖的是隐式框架
    行为而非结构。

    现改为「process_view 只标记不写库，process_response 单条 INSERT」：行要么
    完整出现要么不出现——孤儿行与半成品行结构性消除，每请求少一次数据库往返，
    写失败只告警不抛出（日志是旁路能力，不能把响应打成 500；sqlite 锁表 /
    数据库瞬时故障同理）。
    """
    try:
        return OperationLog.objects.create(**info)
    except Exception as e:
        logger.warning(f"insert operation log failed. error:{e}")
        return None


def resolve_auth_identity(request: Any, response: Any) -> Any:
    """本次请求的凭证标识：``(auth_type, token_pk)``。

    - PAT 请求：``pat`` + 凭证主键（PAT 调用记录/统计据此精确归集）；
    - JWT 请求：``jwt``；
    - 匿名 / 白名单接口（登录、健康检查等）：留空。

    DRF 的 ``request.auth`` 只挂在 DRF Request 包装对象上，而中间件拿到的是原始
    HttpRequest，故优先从渲染上下文的 DRF request 取；认证失败（401）等拿不到
    auth 的场景退化为按 Authorization 头判定类型（不反查凭证，token_pk 留空）。
    """
    # common.core.auth 保持惰性 import：middleware 在 common 层，避免顶层引入认证链造成循环依赖
    from common.core.auth import PersonalAccessTokenAuthentication

    drf_request = None
    if hasattr(response, "renderer_context"):
        drf_request = response.renderer_context.get("request")
    auth = getattr(drf_request, "auth", None) if drf_request is not None else None
    if isinstance(auth, PersonalAccessToken):
        return OperationLog.AuthType.PAT, auth.pk
    header_parts = request.META.get("HTTP_AUTHORIZATION", "").split()
    if header_parts and header_parts[0].lower() == PersonalAccessTokenAuthentication.keyword:
        # 认证被拒（IP 白名单未命中/凭证失效）时认证链已清空 auth：用拒绝前的埋点归集
        rejected_pk = getattr(drf_request, "_pat_rejected_token_pk", None) if drf_request is not None else None
        return OperationLog.AuthType.PAT, rejected_pk
    if auth is not None:
        return OperationLog.AuthType.JWT, None
    user = get_request_user(request)
    if user and not isinstance(user, AnonymousUser):
        return OperationLog.AuthType.JWT, None
    return None, None


def build_operation_log_info(request: Any, response: Any, request_start_time: Any, object_pk: Any = None) -> Any:
    """组装操作日志行（响应阶段一次性求值，单条 INSERT 落库）。

    所有字段在此一次性求值（包括 UA 解析与用户主键），返回值不再持有
    request / ORM 实例引用；``object_pk`` 由 process_view 从 URL kwargs 提取
    后随请求透传（detail 路由的行级变更历史定位，list/create 无 pk 路由留空）。
    """
    limit = _log_field_limit()
    body = desensitize_body(getattr(request, "request_data", {}), request_value_fields(request))
    # 非 dict 响应的整包解析丢弃逻辑已删除——DRF 渲染后的 content
    # 无法可靠还原 data，解析了也不用，只会白白序列化一遍大响应
    response_data = getattr(response, "data", None)
    if response_data is None:
        # 响应缓存命中时返回的是普通 HttpResponse（无 .data）：此时才退化为解析已渲染的
        # JSON 响应体，否则被缓存接口的操作日志会丢失 status_code / response_result。
        # 无 content 的响应对象（含测试替身）不解析，保持"非 dict 不解析"的既有语义。
        content = getattr(response, "content", None)
        content_type = ""
        if hasattr(response, "get"):
            content_type = str(response.get("Content-Type", ""))
        if content and content_type.startswith("application/json"):
            try:
                response_data = json.loads(content)
            except Exception:
                # 响应体非合法 JSON：按无可解析体处理（日志字段降级为空）
                response_data = None
    if not isinstance(response_data, dict):
        response_data = {}
    # 文件下载类响应（流式化后的导出 / 附件）没有 JSON 业务码：HTTP 200 即业务成功，
    # 否则日志页会出现「响应码 200 + 业务码空」的割裂行
    status_code = response_data.get("code")
    if status_code is None and isinstance(response, StreamingHttpResponse) and response.status_code == 200:
        status_code = API_SUCCESS_CODE
    user = get_request_user(request)
    request_module = getattr(request, "request_module", "")
    if hasattr(response, "renderer_context"):
        # 视图实例可能没有与 HTTP 动词同名的方法（如 ViewSet 的 405/detail 误配路径），
        # getattr 必须带兜底，否则操作日志会把业务响应改写成 500
        view = response.renderer_context.get("view")
        handler = getattr(view, request.method.lower(), None) if view else None
        # 视图方法 docstring 只取首行：多行长说明（如 412 交互流程）会撑爆 module 列且不可读
        action_doc = get_doc_first_line(getattr(handler, "__doc__", None)) if handler else ""
        if action_doc:
            try:
                action_doc = action_doc.format(cls=request_module)
            except Exception:
                # docstring 含未知占位符：回退为模块名
                action_doc = request_module
        else:
            action_doc = request_module
    else:
        action_doc = request_module
    auth_type, token_pk = resolve_auth_identity(request, response)
    return {
        "module": action_doc[:OPERATION_LOG_MODULE_MAX] if action_doc else action_doc,
        # 行级变更历史对象定位：detail 路由由中间件从 URL kwargs 提取后透传
        "object_pk": object_pk,
        # 凭证标识：PAT 精确审计的数据基础（按 token_pk 归集调用记录）
        "auth_type": auth_type,
        "token_pk": token_pk,
        # 预取主键而非持有实例：on_commit 回调中不再延迟访问 request/ORM
        "creator_id": getattr(user, "pk", None) if not isinstance(user, AnonymousUser) else None,
        "dept_belong_id": getattr(request.user, "dept_id", None),
        "ipaddress": request.request_ip,
        "method": request.method,
        "path": request.path,
        "body": json.dumps(body, default=str)[:limit] if isinstance(body, dict) else str(body)[:limit],
        "response_code": response.status_code,
        # Step2：UA 只解析一次（旧实现 get_os/get_browser 各跑一次重型正则）
        "system": get_os(request),
        "browser": get_browser(request),
        "status_code": status_code,
        "request_uuid": getattr(request, "request_uuid", None),
        "exec_time": time.time() - request_start_time,
        # 字段级变更 diff（AUDIT_DIFF_MODELS 白名单模型的 update 路径由视图集挂载）。
        # 写入前先跑凭据名脱敏（password / token 等字段名，与请求体同口径），字段级
        # 业务脱敏（手机号等）由输出侧按源模型规则完成（见 audit.serializers.log）。
        "changes": json.dumps(desensitize_payload(changes), cls=encoders.JSONEncoder, default=str)[:limit]
        if (changes := getattr(request, "operation_log_changes", None))
        else None,
        # 变更来源模型：输出侧据此套用源模型的脱敏规则
        "model_label": str(getattr(request, "operation_log_model_label", "") or "")[:128],
        # 响应体同口径脱敏：登录响应（access/refresh）与临时令牌响应（token）等
        # 敏感值不落操作日志（与请求体 body 共用 desensitize_payload）
        "response_result": json.dumps(
            {
                "code": response_data.get("code"),
                "data": desensitize_payload(response_data.get("data")),
                "detail": response_data.get("detail"),
            },
            cls=encoders.JSONEncoder,
            default=str,
        )[:limit],
    }
