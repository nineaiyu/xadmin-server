#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""操作日志落库辅助：大字段截断 / 脱敏 / 身份解析 / 信息装配（自 middleware.py 拆分，行为不变）。"""

import json
import time

from django.contrib.auth.models import AnonymousUser
from rest_framework.utils import encoders

from common.core.config import SysConfig
from common.core.utils import get_doc_first_line
from common.utils import get_logger
from common.utils.request import (
    get_browser,
    get_os,
    get_request_user,
)
from system.services import OperationLog, PersonalAccessToken

logger = get_logger(__name__)


# 日志大字段截断上限（系统配置 OPERATION_LOG_FIELD_MAX 的默认值），
# 避免大请求体/大响应整包入库；运行期取值见 _log_field_limit()
MAX_LOG_FIELD = 4096
# 操作日志脱敏字段清单（按**键名**匹配，递归生效——请求体与响应体共用）
# code：二次验证提交体里的登录密码/动态验证码（POST /api/mfa/confirm 等），
# token / verify_token：临时令牌与验证码票据（登录/注册/重置/绑定加密握手），
# access / refresh：登录响应里的 JWT（响应快照同口径收敛），
# 严禁明文落日志
SENSITIVE_FIELDS = {
    "password",
    "old_password",
    "new_password",
    "sure_password",
    "access",
    "refresh",
    "code",
    "token",
    "verify_token",
}


def _log_field_limit():
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


def desensitize_payload(value):
    """递归脱敏：dict 按键名掩码、list 逐项处理，其余（标量 / 非容器）原样返回。

    请求体（含嵌套 payload、嵌套 list）与**响应体**共用同一口径——登录响应的
    ``access`` / ``refresh`` 与临时令牌响应的 ``token`` 都在此收敛，不再明文
    进操作日志的 ``body`` / ``response_result`` 与 DEBUG/慢请求日志。
    掩码保留长度信息（与原值等长），便于排障时识别字段是否为空。
    """
    if isinstance(value, dict):
        return {
            key: ("*" * len(str(item)) if key in SENSITIVE_FIELDS and item else desensitize_payload(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [desensitize_payload(item) for item in value]
    return value


def desensitize_body(body):
    """对请求体中的敏感字段做掩码处理（递归入口，兼容既有调用点）。"""
    return desensitize_payload(body)


def log_body_preview(payload) -> str:
    """日志正文预览：脱敏 + 按大字段上限截断（DEBUG / 慢请求日志共用）。

    ``OPERATION_LOG_FIELD_MAX=0``（不落大字段）时同样不落正文——配置意图对
    运行日志一致；脱敏先于截断，避免截断把敏感串留在前缀里。
    """
    limit = _log_field_limit()
    text = json.dumps(desensitize_payload(payload), cls=encoders.JSONEncoder, default=str)
    return text[:limit] if limit else ""


def write_operation_log(operation_log_id, info):
    """主键已知，用 UPDATE 替代 update_or_create（省 1 条 SELECT）。

    该函数通过 transaction.on_commit 在请求事务提交后执行，
    请求事务回滚时占位行一并消失，UPDATE 影响 0 行，不再产生孤儿日志写；
    事务中断也不会再连带整个请求失败。
    """
    try:
        OperationLog.objects.filter(id=operation_log_id).update(**info)
    except Exception as e:  # sqlite3 数据库因为锁表可能会导致日志记录失败
        logger.warning(f"write operation log failed. id:{operation_log_id} error:{e}")


def resolve_auth_identity(request, response):
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


def build_operation_log_info(request, response, request_start_time):
    """组装操作日志字段。

    所有字段在此一次性求值（包括 UA 解析与用户主键），返回值不再持有
    request / ORM 实例引用，因此可以安全地延迟到 on_commit 回调中执行。
    """
    limit = _log_field_limit()
    body = desensitize_body(getattr(request, "request_data", {}))
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
        "status_code": response_data.get("code"),
        "request_uuid": getattr(request, "request_uuid", None),
        "exec_time": time.time() - request_start_time,
        # 字段级变更 diff（AUDIT_DIFF_MODELS 白名单模型的 update 路径由视图集挂载）
        "changes": json.dumps(changes, cls=encoders.JSONEncoder, default=str)[:limit]
        if (changes := getattr(request, "operation_log_changes", None))
        else None,
        # 响应体同口径脱敏：登录响应（access/refresh）与临时令牌响应（token）等
        # 敏感值不落操作日志（与请求体 body 共用 desensitize_payload，ADR-072）
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
