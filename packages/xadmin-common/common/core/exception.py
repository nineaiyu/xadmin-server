#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : exception
# author : ly_13
# date : 6/2/2023
import traceback
from logging import getLogger
from typing import Any

from django.db.models import ProtectedError
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException, Throttled
from rest_framework.views import exception_handler, set_rollback
from rest_framework_simplejwt.exceptions import InvalidToken

from common.core.response import ApiResponse
from common.settings_contract import kernel_setting

logger = getLogger("drf_exception")
unexpected_exception_logger = getLogger("unexpected_exception")


class ReadableThrottled(Throttled):
    """带可读业务文案的限流异常：全局处理器采用其 detail，不归一为通用「手速太快」。

    DRF 原生 Throttled 在本项目统一归一为前端友好文案；但开放平台的
    **应用限流 / 每日配额**需要给调用方可区分的语义（等一分钟还是等明天），
    故由本子类显式声明「文案权威」。
    """


def common_exception_handler(exc: Any, context: Any) -> Any:
    if kernel_setting("DEBUG_DEV"):
        logger.exception("Print traceback exception for Debug")
        traceback.print_exc()

    # context['view'] 是 TextView 的对象，想拿出这个对象对应的类名；
    # 缺失时（非视图链路）不能让错误处理器自身抛 KeyError
    view = context.get("view")
    view_name = view.__class__.__name__ if view is not None else "unknown"
    ret = exception_handler(exc, context)  # 是Response对象，它内部有个data
    logger.error(f"{view_name} ERROR: {exc} ret:{ret}")
    # 各分支显式指定的业务码，优先于 HTTP 状态码写入响应体（见函数末尾）
    business_code = None
    if isinstance(exc, ReadableThrottled):
        business_code = 999
        ret.data = {"code": 999, "detail": exc.detail}
    elif isinstance(exc, Throttled):
        if not exc.wait:
            detail = _("Your visit is too fast, please visit again later")
        else:
            detail = _("Your visit is too fast, please visit again in {} seconds").format(exc.wait)
        business_code = 999
        ret.data = {"code": 999, "detail": detail}

    elif isinstance(exc, APIException):
        if isinstance(exc, InvalidToken):
            if isinstance(exc.detail, dict) and "messages" in exc.detail:
                ret.code = 40001  # access token 失效或者过期
                del exc.detail["messages"]
            else:
                ret.code = 40002  # refresh token 失效或者过期

        # 浅拷贝后再写入 status/code/errors：避免污染异常自身的 detail 容器
        if isinstance(exc.detail, dict):
            ret.data = dict(exc.detail)
        elif isinstance(exc.detail, list):
            ret.data = list(exc.detail)
        else:
            ret.data = {"detail": exc.detail}
        set_rollback()

    elif isinstance(exc, Http404):
        ret.status_code = 400
        ret.data = {"detail": _("The requested address is incorrect or the data permission is not allowed")}

    elif isinstance(exc, ProtectedError):
        set_rollback()
        # pop() 会改动异常自身、对空集合还会抛 KeyError：缺失时降级为通用文案
        protected = next(iter(exc.protected_objects), None)
        verbose_name = protected._meta.verbose_name if protected is not None else _("data")
        # HTTP 400 + 业务码 998：与同一处理器其它分支的 HTTP 语义对齐——原为 200，
        # 客户端错误被伪装成成功响应，网关/监控无法按状态码统计；前端全局兜底
        # （errorStrategies 的 400 策略）读取 detail 提示，业务码 998 语义不变
        return ApiResponse(
            code=998,
            status=400,
            detail=_("Is referenced by other {} and cannot be deleted").format(verbose_name),
        )
    else:
        unexpected_exception_logger.exception("")

    if not ret:  # drf内置处理不了，丢给django 的，我们自己来处理
        # 未预期异常不向客户端暴露内部细节（完整堆栈已由 unexpected_exception_logger 记录）
        return ApiResponse(
            detail=_("Server internal error, please contact administrator or try again later"), code=500, status=500
        )
    else:
        if isinstance(ret.data, list):
            ret.data = {"detail": ret.data}
        if not ret.data.get("detail"):
            # 字段级校验错误（{field: [errors]}）拼成可读文案；
            # 结构化错误保留在 errors 中，供前端做表单内联展示。
            # 注意：不回退到 str(exc)——异常原文可能带内部实现细节，只进服务端日志。
            fallback_detail = _("Operation failed, please check the submitted data")
            if isinstance(ret.data, dict):
                errors = {k: v for k, v in ret.data.items() if k not in ("status", "code", "errors")}
                ret.data["errors"] = errors
                ret.data["detail"] = (
                    "; ".join(
                        f"{key}: {'; '.join(map(str, value)) if isinstance(value, (list, tuple)) else value}"
                        for key, value in errors.items()
                    )
                    or fallback_detail
                )
            else:
                ret.data["detail"] = fallback_detail
        ret.data["status"] = ret.status_code
        # 业务码优先：Throttled 的 999 / InvalidToken 的 40001 不被 HTTP 状态码覆盖
        ret.data["code"] = business_code or (ret.code if hasattr(ret, "code") else ret.status_code)
        return ApiResponse(**ret.data)
