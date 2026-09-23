#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""文件访问审计与上传安全策略（F-8）。

- ``log_file_access``：上传 / 下载 / 预览 / 删除四类动作的元数据留痕
  （日志写入全程吞异常，绝不阻断文件主链路）；
- ``validate_upload_extension``：上传扩展名策略（黑名单优先，白名单非空时只允许名单内），
  fail-closed；用于通用文件上传（图片字段上传另有独立的魔数 + 重编码链路）。
"""

import os

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from common.utils import get_logger

logger = get_logger(__name__)


def log_file_access(*, upload=None, user=None, action, request=None, result=True, detail="", filename=""):
    """写一条文件访问日志（失败只记 warning）。"""
    from system.models import FileAccessLog

    try:
        from common.utils.request import get_request_ip
        from system.utils.approval.display import user_display

        ip = get_request_ip(request) if request is not None else ""
        FileAccessLog.objects.create(
            file=upload,
            filename=(filename or getattr(upload, "filename", "") or "")[:255],
            user=user if getattr(user, "pk", None) else None,
            user_display=user_display(user) if getattr(user, "pk", None) else "",
            action=action,
            ipaddress=ip or "",
            result=bool(result),
            detail=(detail or "")[:255],
        )
    except Exception:  # noqa: BLE001 审计写入失败不影响文件主链路
        logger.warning("write file access log failed. action:%s", action, exc_info=True)


def _extension_list(value):
    return {str(item).strip().lower().lstrip(".") for item in (value or []) if str(item).strip()}


def get_upload_policy():
    """当前上传扩展名策略（黑名单 / 白名单），供前端提示与校验同源。"""
    return {
        "block_extensions": sorted(_extension_list(getattr(settings, "SECURITY_UPLOAD_BLOCK_EXTENSIONS", []))),
        "allow_extensions": sorted(_extension_list(getattr(settings, "SECURITY_UPLOAD_ALLOW_EXTENSIONS", []))),
    }


def validate_upload_extension(filename) -> str:
    """校验上传文件扩展名，返回错误文案（通过返回空串）。

    fail-closed 规则：白名单非空 → 必须命中白名单；命中黑名单一律拒绝（黑名单优先，
    防止「白名单里误加 exe」类配置把危险类型放进来）。
    """
    ext = os.path.splitext(str(filename or ""))[1].lower().lstrip(".")
    policy = get_upload_policy()
    if ext in set(policy["block_extensions"]):
        return str(_("File type .{} is not allowed to upload").format(ext or "?"))
    allow = set(policy["allow_extensions"])
    if allow and ext not in allow:
        return str(_("Only the following file types are allowed: {}").format(", ".join(sorted(allow))))
    return ""


# 清理批次大小（与操作日志清理同量级）
FILE_LOG_CLEAN_BATCH = 2000


def clean_expired_file_access_logs():
    """按 FILE_ACCESS_LOG_KEEP_DAYS 分批清理文件访问日志（0 = 不清理），返回删除行数。"""
    from datetime import timedelta

    from django.utils import timezone

    from system.models import FileAccessLog

    keep_days = int(getattr(settings, "FILE_ACCESS_LOG_KEEP_DAYS", 180) or 0)
    if keep_days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=keep_days)
    total = 0
    while True:
        pks = list(
            FileAccessLog.objects.filter(created_time__lt=cutoff).values_list("pk", flat=True)[:FILE_LOG_CLEAN_BATCH]
        )
        if not pks:
            break
        FileAccessLog.objects.filter(pk__in=pks).delete()
        total += len(pks)
    logger.info("clean %s file access logs (keep %s days)", total, keep_days)
    return total
