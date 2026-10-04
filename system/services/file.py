#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""文件域服务：个人文件统计聚合 与 在线预览状态机。

视图层（``system/views/admin/file.py``）保留鉴权（``get_object``）、审计留痕与
HTTP 响应构造；统计聚合与预览状态判定收口到本模块，聊天附件等消费方复用
同一套口径。

预览状态机以 ``(state, payload)`` 二元组表达判定结果，视图按状态映射响应：
不支持预览 → 业务码 1005；源文件缺失 → 1001；文本 → ``text/plain``（截断标记
走 ``X-Preview-Truncated`` 响应头）；图片 → inline JPEG（缩略图/预览缓存）；
PDF → 原样 inline；Office → 转换产物 inline / 转换中回 1006（HTTP 425）由
前端重试。
"""

import datetime

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from common.storage import storage_exists, storage_open
from system.models import UploadFile
from system.utils.file.preview import (
    KIND_IMAGE,
    KIND_OFFICE,
    KIND_PDF,
    KIND_TEXT,
    PREVIEW_STATUS_PREPARING,
    PREVIEW_STATUS_READY,
    SIZE_THUMB,
    ensure_image_cache,
    ensure_office_pdf,
    read_text_preview,
    touch_preview_cache,
)
from system.utils.file.upload_category import UPLOAD_CATEGORY_DICT
from system.utils.file.upload_store import get_user_quota_mb
from system.utils.platform.dict import get_dict_items

# 不支持在线预览的业务码：前端按该码禁用预览按钮并说明原因
PREVIEW_UNSUPPORTED_CODE = 1005
# Office 转换中的业务码：前端稍后重试预览请求
PREVIEW_PREPARING_CODE = 1006

# 预览状态机判定结果（视图按状态映射 HTTP 响应）
PREVIEW_STATE_UNSUPPORTED = "unsupported"  # 类型不支持或内容不可读 → 1005
PREVIEW_STATE_FILE_MISSING = "file_missing"  # 源文件缺失 → 1001 File not found
PREVIEW_STATE_TEXT = "text"  # (content, truncated) → text/plain 响应
PREVIEW_STATE_IMAGE = "image"  # 缓存路径 → inline image/jpeg
PREVIEW_STATE_PDF = "pdf"  # (流, mime) → inline
PREVIEW_STATE_OFFICE_READY = "office_ready"  # 转换产物路径 → inline application/pdf
PREVIEW_STATE_OFFICE_PREPARING = "office_preparing"  # 转换中 → 1006/425


# ---------------------------------------------------------------- 统计聚合


def build_personal_file_stats(user) -> dict:
    """个人文件统计（数量/总大小/配额使用率 + 分类分布/近 7 天趋势/最大文件）。

    顶部统计面板的数据源：列表口径（活动记录）+ 一次聚合出多组维度，
    调用方以 10s 短缓存兜住重复刷新。
    """
    # 配额按上传人维度聚合（creator 索引），与管理页「我的文件」口径一致
    queryset = UploadFile.objects.filter(creator=user)
    agg = queryset.aggregate(count=Count("pk"), total_size=Sum("filesize"))
    count = agg["count"] or 0
    total_size = agg["total_size"] or 0
    # 配额概览（存储用量/文件数）：个人行优先，未设置继承系统级（0 = 不限）
    quota_mb = get_user_quota_mb(user) or 0
    quota_bytes = quota_mb * 1024 * 1024
    usage_rate = round(total_size / quota_bytes * 100, 2) if quota_bytes else 0
    return {
        "count": count,
        "total_size": total_size,
        "quota_mb": quota_mb,
        "usage_rate": usage_rate,
        # 剩余空间：无配额（0=不限）时给 null，前端显示「不限」
        "remaining_size": max(quota_bytes - total_size, 0) if quota_bytes else None,
        "avg_size": round(total_size / count) if count else 0,
        "category_stats": _category_stats(queryset),
        "recent_trend": _recent_trend(queryset),
        "top_files": list(queryset.order_by("-filesize").values("pk", "filename", "filesize")[:5]),
    }


def _category_stats(queryset):
    """分类分布（数量/大小）：label/color 取自字典，字典缺失时回退分类 code。

    `value=None` 表示未分类（历史数据），label/color 一并给 null，
    展示文案（「未分类」）由前端 i18n 负责，不写进缓存载荷。
    """
    dict_items = {item["value"]: item for item in get_dict_items(UPLOAD_CATEGORY_DICT)}
    rows = queryset.values("category").annotate(count=Count("pk"), size=Sum("filesize")).order_by("-size")
    result = []
    for row in rows:
        item = dict_items.get(row["category"]) or {}
        result.append(
            {
                "value": row["category"],
                # 字典项已删除的历史值：label 回退 code 本身，保证图表仍可读
                "label": item.get("label") or row["category"],
                "color": item.get("color"),
                "count": row["count"],
                "size": row["size"] or 0,
            }
        )
    return result


def _recent_trend(queryset, days=7):
    """近 N 天上传趋势（按天聚合，空缺日期补 0，前端无需再做日历运算）。

    日期口径与 Django 当前时区一致（TruncDate 走 USE_TZ 时区转换）。
    """
    today = timezone.localdate()
    start = today - datetime.timedelta(days=days - 1)
    rows = (
        queryset.filter(created_time__date__gte=start)
        .annotate(day=TruncDate("created_time"))
        .values("day")
        .annotate(count=Count("pk"), size=Sum("filesize"))
    )
    by_day = {row["day"]: row for row in rows}
    trend = []
    for offset in range(days):
        day = start + datetime.timedelta(days=offset)
        row = by_day.get(day) or {}
        trend.append({"date": day.isoformat(), "count": row.get("count", 0), "size": row.get("size") or 0})
    return trend


# ---------------------------------------------------------------- 预览状态机


def resolve_preview(upload, kind, size=None) -> tuple:
    """预览状态判定：按 ``preview_kind`` 结果分派，返回 ``(state, payload)``。

    ``kind`` 由调用方传入（视图先经 ``preview_kind`` 取值用于访问审计明细，
    复用同一判定结果）；``size`` 仅图片态生效（``?size=thumb|preview``，
    缺省 thumb）。
    """
    if kind is None or not upload.filepath:
        return PREVIEW_STATE_UNSUPPORTED, None

    if kind == KIND_TEXT:
        content, truncated = read_text_preview(upload)
        if not content:
            return PREVIEW_STATE_UNSUPPORTED, None
        return PREVIEW_STATE_TEXT, (content, truncated)

    if kind == KIND_IMAGE:
        cache_path = ensure_image_cache(upload, size or SIZE_THUMB)
        if not cache_path:
            return PREVIEW_STATE_UNSUPPORTED, None
        touch_preview_cache(cache_path)
        return PREVIEW_STATE_IMAGE, cache_path

    # PDF：原样 inline 返回（浏览器内嵌渲染，不生成缓存）
    if kind == KIND_PDF:
        # 存储适配：对象存储无本地路径，统一走 storage 原语
        name = getattr(upload.filepath, "name", "")
        if not name or not storage_exists(name):
            return PREVIEW_STATE_FILE_MISSING, None
        return PREVIEW_STATE_PDF, (storage_open(name, "rb"), upload.mime_type or "application/pdf")

    # Office：转换产物就绪即 inline 返回；转换中回 1006 由前端重试
    if kind == KIND_OFFICE:
        path, status = ensure_office_pdf(upload)
        if status == PREVIEW_STATUS_READY and path:
            touch_preview_cache(path)
            return PREVIEW_STATE_OFFICE_READY, path
        if status == PREVIEW_STATUS_PREPARING:
            return PREVIEW_STATE_OFFICE_PREPARING, None

    return PREVIEW_STATE_UNSUPPORTED, None
