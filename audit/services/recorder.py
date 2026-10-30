"""审计写入统一入口：``record()`` 收敛各域散落的 ``OperationLog.objects.create`` 重复实现。

此前凭据轮换、聊天撤回、建号被拒、AI 动作/问答、演示 push 等各自维护一份
try/except + json 序列化 + 截断的写入样板，口径极易漂移（漏截断打爆 varchar、
忘写 creator 解析、异常冒泡阻断业务）。本模块提供唯一稳定落库面：

- ``changes`` 传 dict，统一 ``json.dumps(ensure_ascii=False, default=str)`` 后按
  :data:`CHANGES_MAX_LENGTH` 截断（TextField 无库级上限，沿用各调用点 4096 口径）；
- creator / modifier 统一解析：对象有 pk 才写（匿名 / 未落库对象记 NULL）；
- 文本字段按模型 max_length 截断（PostgreSQL 对 varchar 超长直接报错）；
- 任何失败只 ``logger.warning``，绝不向上抛——审计是旁路，不阻断业务主流程。

调用面：``from audit.services import record``（经契约层惰性导出）。
"""

import json
from typing import Any

from audit.models import OperationLog
from common.utils import get_logger

logger = get_logger(__name__)

#: changes JSON 文本的统一截断长度（既有各调用点口径）
CHANGES_MAX_LENGTH = 4096


def _truncate(value: Any, limit: int) -> str:
    """转 str 并按模型 max_length 截断（varchar 超长会被 PostgreSQL 拒绝）。"""
    text = value if isinstance(value, str) else str(value or "")
    return text[:limit]


def record(
    *,
    module: str,
    object_pk: Any = None,
    path: str | None = None,
    method: str | None = None,
    ipaddress: Any = None,
    body: str | None = None,
    response_result: str | None = None,
    changes: dict[str, Any] | None = None,
    status_code: int | None = None,
    response_code: int | None = None,
    auth_type: Any = None,
    creator: Any = None,
    modifier: Any = None,
) -> None:
    """审计写入统一入口（module 必填，其余按需；失败只告警不抛）。

    :param module: 审计模块标签（如 ``system:credential``），截断 64
    :param object_pk: 行级对象定位，转 str 截断 64；None = 不写该列
    :param path: 请求路径（截断 400）；method: 请求方法（截断 8）
    :param ipaddress: 客户端 IP（原样透传，由调用方解析）
    :param body: 请求/快照正文（TextField 不限长，原样透传）
    :param response_result: 响应摘要（TextField 不限长，原样透传）
    :param changes: 结构化变更 dict，统一序列化（ensure_ascii=False + default=str）
        并按 :data:`CHANGES_MAX_LENGTH` 截断
    :param status_code / response_code: 业务码 / HTTP 状态码（按调用点口径传入）
    :param auth_type: 凭证类型（如 ``OperationLog.AuthType.AI``，截断 16）
    :param creator / modifier: 操作人，统一解析（有 pk 才写，否则 NULL）
    """
    # creator/modifier 恒写（含 None）：与既有各调用点显式传参的落库形态一致
    fields: dict[str, Any] = {
        "module": _truncate(module, 64),
        "creator": creator if getattr(creator, "pk", None) else None,
        "modifier": modifier if getattr(modifier, "pk", None) else None,
    }
    if object_pk is not None:
        fields["object_pk"] = _truncate(object_pk, 64)
    if path is not None:
        fields["path"] = _truncate(path, 400)
    if method is not None:
        fields["method"] = _truncate(method, 8)
    if auth_type is not None:
        fields["auth_type"] = _truncate(auth_type, 16)
    # 以下字段不做改写：IP 由调用方解析、TextField 与数值列原样透传
    for name, value in (
        ("ipaddress", ipaddress),
        ("body", body),
        ("response_result", response_result),
        ("status_code", status_code),
        ("response_code", response_code),
    ):
        if value is not None:
            fields[name] = value
    try:
        # changes 序列化与落库同界：循环引用等序列化异常与建行失败一并按「只告警」处理
        if changes is not None:
            fields["changes"] = json.dumps(changes, ensure_ascii=False, default=str)[:CHANGES_MAX_LENGTH]
        OperationLog.objects.create(**fields)
    except Exception:  # noqa: BLE001 审计失败不影响业务主流程
        logger.warning("write operation audit record failed", exc_info=True)
