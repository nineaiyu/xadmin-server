#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : log_archive
"""审计日志冷归档与冷热分层。

策略（**归档水位驱动清理**，保证「删必已归档」）：

1. **归档**：``月末 <= now - retention_days`` 的整月（含成功与错误日志）各导出一份
   ``<model>-<YYYY-MM>.jsonl.gz``（未压缩 JSONL 的 sha256 落 ``.sha256`` sidecar，
   行数 / 时间范围 / 校验和落 ``.manifest.json``）；同月已归档且校验通过则跳过（幂等）；
2. **清理**：删除边界 = **已归档水位**（从系统最早数据起「连续已归档且整月超期」的边界）
   与保留窗口的较小值——成功日志按全量保留期、错误日志按错误保留期（分层留存语义不变）；
   未归档的边界月不删除（最多多留一个月，保留期是下限）；
3. **恢复查询**：``iter_restore_rows`` 流式读取归档（不落库、不建临时表），
   支撑「半年前谁改的」类追溯与季度演练（「从冷归档恢复查询」）；
4. **校验**：``verify_archive`` 重新计算 sha256 与行数，供巡检（``--verify``）。

归档目录：``settings.LOG_ARCHIVE_DIR``（默认 ``DATA_DIR/log_archive``，环境变量
``LOG_ARCHIVE_DIR`` 可覆盖为备份卷路径以便纳入异地同步；注意备份脚本 ``prune_local``
会按 ``*.sql.gz / *.media.tar.gz / *.sha256`` 后缀清理备份卷存量文件，
**不要把归档直接放在备份卷根目录**，见 docs/ops/pitr.md）。
"""

import datetime
import gzip
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

ARCHIVE_VERSION = 1
PRUNE_BATCH_SIZE = 2000
ARCHIVE_MODEL_KEYS = ("operation", "login")

# 归档表（延后导入避免 AppRegistry 未就绪）
_MODEL_PATHS = {"operation": "system.OperationLog", "login": "system.UserLoginLog"}


def archive_root(directory=None) -> Path:
    root = Path(directory or settings.LOG_ARCHIVE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def model_for(model_key: str):
    from django.apps import apps

    if model_key not in _MODEL_PATHS:
        raise ValueError(f"不支持的归档对象：{model_key}（可选 {ARCHIVE_MODEL_KEYS}）")
    return apps.get_model(_MODEL_PATHS[model_key])


def month_of(value: datetime.datetime) -> str:
    """按本地时区取 ``YYYY-MM``（审计口径与用户感知一致）。"""
    local = timezone.localtime(value)
    return f"{local.year:04d}-{local.month:02d}"


def parse_month(month: str) -> tuple[datetime.datetime, datetime.datetime]:
    """``YYYY-MM`` → 本地时区的（月初，下月初）区间。"""
    try:
        year, mon = (int(part) for part in month.split("-"))
        first = datetime.datetime(year, mon, 1)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"月份格式应为 YYYY-MM，收到 {month!r}") from exc
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(first, tz)
    end = timezone.make_aware(
        datetime.datetime(year + (mon // 12), (mon % 12) + 1, 1),
        tz,
    )
    return start, end


def next_month(month: str) -> str:
    _, end = parse_month(month)
    return month_of(end)


def _paths(model_key: str, month: str, directory=None) -> tuple[Path, Path, Path]:
    root = archive_root(directory)
    stem = f"{model_key}-{month}"
    return root / f"{stem}.jsonl.gz", root / f"{stem}.sha256", root / f"{stem}.manifest.json"


def _json_default(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def _row_to_line(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, default=_json_default, separators=(",", ":"))


def iter_rows(model, start, end) -> Iterator[dict]:
    fields = [field.name for field in model._meta.concrete_fields]
    queryset = model.objects.filter(created_time__gte=start, created_time__lt=end).order_by("pk").values(*fields)
    yield from queryset.iterator(chunk_size=PRUNE_BATCH_SIZE)


def archive_month(model_key: str, month: str, directory=None, dry_run: bool = False) -> dict:
    """归档单个整月；已归档且校验通过时幂等跳过（返回 manifest 并附 skipped=True）。"""
    model = model_for(model_key)
    start, end = parse_month(month)
    gz_path, sha_path, manifest_path = _paths(model_key, month, directory)
    if gz_path.exists() and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {**manifest, "skipped": True}

    if dry_run:
        rows = model.objects.filter(created_time__gte=start, created_time__lt=end).count()
        return {"model": model_key, "month": month, "rows": rows, "dry_run": True}

    tmp_path = gz_path.with_name(gz_path.name + ".tmp")
    digest = hashlib.sha256()
    rows = 0
    first_time = last_time = None
    min_pk = max_pk = None
    with open(tmp_path, "wb") as raw, gzip.open(raw, "wb") as gz:
        for row in iter_rows(model, start, end):
            line = _row_to_line(row) + "\n"
            data = line.encode("utf-8")
            gz.write(data)
            digest.update(data)
            rows += 1
            created = row.get("created_time")
            if created is not None:
                first_time = first_time or created
                last_time = created
            pk = row.get("id")
            if pk is not None:
                min_pk = pk if min_pk is None else min(min_pk, pk)
                max_pk = pk if max_pk is None else max(max_pk, pk)
    os.replace(tmp_path, gz_path)

    size = gz_path.stat().st_size
    checksum = digest.hexdigest()
    sha_path.write_text(f"{checksum}  {gz_path.name}\n", encoding="utf-8")
    manifest = {
        "archive_version": ARCHIVE_VERSION,
        "model": model_key,
        "month": month,
        "rows": rows,
        "bytes": size,
        "sha256": checksum,
        "first_time": first_time.isoformat() if first_time else None,
        "last_time": last_time.isoformat() if last_time else None,
        "min_pk": str(min_pk) if min_pk is not None else None,
        "max_pk": str(max_pk) if max_pk is not None else None,
        "created_time": timezone.now().isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("归档 %s %s：%s 行 / %s 字节（%s）", model_key, month, rows, size, checksum[:12])
    return manifest


def list_archives(directory=None) -> list[dict]:
    root = archive_root(directory)
    manifests = []
    for path in sorted(root.glob("*.manifest.json")):
        try:
            manifests.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            logger.warning("归档清单不可读，已跳过：%s", path)
    return manifests


def archived_months(model_key: str, directory=None) -> set[str]:
    return {item["month"] for item in list_archives(directory) if item.get("model") == model_key}


def retention_days(model_key: str, override=None) -> int:
    """归档 / 清理保留期（天；0 = 该对象不清理）。

    - 操作日志：``OPERATION_LOG_RETENTION_DAYS``（错误日志另有更长的分层保留期）；
    - 登录日志：``LOGIN_LOG_RETENTION_DAYS``（默认 365；0 = 只支持手动归档）。
    """
    from common.core.config import SysConfig

    if override is None:
        if model_key == "login":
            override = SysConfig.LOGIN_LOG_RETENTION_DAYS
        else:
            override = SysConfig.OPERATION_LOG_RETENTION_DAYS
    return override or 0


#: 兼容别名（既有内部调用点使用私有名）
_retention_days = retention_days


def _error_retention_days(retention_days=None) -> int:
    from common.core.config import SysConfig

    if retention_days is None:
        retention_days = SysConfig.OPERATION_LOG_ERROR_RETENTION_DAYS
    return retention_days or 0


def archive_expired(model_key: str = "operation", retention_days=None, directory=None, dry_run: bool = False) -> dict:
    """归档全部「整月已超保留期」的月份（幂等），返回本次归档 / 跳过的清单。"""
    model = model_for(model_key)
    days = _retention_days(model_key, retention_days)
    if days <= 0:
        return {"model": model_key, "archived": [], "skipped": [], "reason": "保留期未启用（0 = 不清理）"}
    clean_time = timezone.now() - datetime.timedelta(days=days)

    earliest = model.objects.order_by("created_time").values_list("created_time", flat=True).first()
    if earliest is None:
        return {"model": model_key, "archived": [], "skipped": [], "reason": "无数据"}
    exists = archived_months(model_key, directory)

    archived, skipped = [], []
    cursor = month_of(earliest)
    guard = 0
    while guard < 600:  # 防御：最多回溯 50 年
        guard += 1
        start, end = parse_month(cursor)
        if end > clean_time:  # 该月尚未整月超期（边界月，交下一轮）
            break
        if cursor in exists:
            skipped.append(cursor)
        elif model.objects.filter(created_time__gte=start, created_time__lt=end).exists():
            archived.append(archive_month(model_key, cursor, directory=directory, dry_run=dry_run))
        else:
            # 空月不生成归档文件；水位推进时按「无数据 = 已覆盖」处理
            skipped.append(cursor)
        cursor = next_month(cursor)
    logger.info(
        "归档检查 %s：新归档 %s 个月，跳过 %s 个月（保留期 %s 天）",
        model_key,
        len(archived),
        len(skipped),
        days,
    )
    return {"model": model_key, "archived": archived, "skipped": skipped}


def archive_watermark(model_key: str = "operation", retention_days=None, directory=None) -> datetime.datetime | None:
    """可安全删除的时间上界：从系统最早数据起「连续已归档且整月超期」的边界。

    未归档（或未整月超期 / 空月未归档）的月份一律不越过——清理不会删掉未归档数据。
    """
    model = model_for(model_key)
    days = _retention_days(model_key, retention_days)
    if days <= 0:
        return None
    clean_time = timezone.now() - datetime.timedelta(days=days)
    earliest = model.objects.order_by("created_time").values_list("created_time", flat=True).first()
    if earliest is None:
        return None
    exists = archived_months(model_key, directory)

    cursor = month_of(earliest)
    watermark = None
    guard = 0
    while guard < 600:  # 防御：最多回溯 50 年
        guard += 1
        start, end = parse_month(cursor)
        if end > clean_time:  # 该月尚未整月超期（边界月）：水位停在该月月初
            return watermark or start
        if cursor not in exists:
            # 空月（无数据）视为已覆盖可继续推进；有数据却没归档则水位停在该月月初
            if model.objects.filter(created_time__gte=start, created_time__lt=end).exists():
                return watermark or start
        watermark = end
        cursor = next_month(cursor)
    return watermark


def prune_archived(
    model_key: str = "operation",
    retention_days=None,
    error_retention_days=None,
    directory=None,
    batch_size: int = PRUNE_BATCH_SIZE,
) -> int:
    """删除已归档且超保留期的日志（水位驱动，删必已归档）；返回删除行数。"""
    model = model_for(model_key)
    watermark = archive_watermark(model_key, retention_days, directory)
    if watermark is None:
        return 0
    days = _retention_days(model_key, retention_days)
    clean_time = timezone.now() - datetime.timedelta(days=days)
    success_cutoff = min(watermark, clean_time)
    error_days = _error_retention_days(error_retention_days)
    if error_days <= days:  # 0/空/不大于全量保留期：分层关闭
        error_cutoff = success_cutoff
    else:
        error_cutoff = min(watermark, timezone.now() - datetime.timedelta(days=error_days))

    total = 0

    def _delete(queryset) -> None:
        nonlocal total
        while True:
            pks = list(queryset.values_list("pk", flat=True)[:batch_size])
            if not pks:
                break
            with transaction.atomic():
                deleted, _rows = model.objects.filter(pk__in=pks).delete()
            total += deleted

    if model_key == "operation":
        # 分层留存：先删过全量保留期的成功日志，再删过错误保留期的剩余（错误）日志
        _delete(
            model.objects.filter(created_time__lt=success_cutoff).filter(
                Q(status_code=1000) | Q(status_code__isnull=True)
            )
        )
        _delete(model.objects.filter(created_time__lt=error_cutoff))
    else:
        _delete(model.objects.filter(created_time__lt=success_cutoff))
    logger.info(
        "清理 %s：删除 %s 行（水位 %s / 全量边界 %s / 错误边界 %s）",
        model_key,
        total,
        watermark.isoformat(),
        success_cutoff.isoformat(),
        error_cutoff.isoformat(),
    )
    return total


def verify_archive(model_key: str, month: str, directory=None) -> dict:
    """校验归档完整性：文件存在、sha256 一致、行数与清单一致。"""
    gz_path, sha_path, manifest_path = _paths(model_key, month, directory)
    if not gz_path.exists() or not manifest_path.exists():
        return {"model": model_key, "month": month, "ok": False, "reason": "归档文件或清单缺失"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    rows = 0
    with gzip.open(gz_path, "rb") as gz:
        for raw_line in gz:
            digest.update(raw_line)
            rows += 1
    checksum = digest.hexdigest()
    sidecar = sha_path.read_text(encoding="utf-8").split()[0] if sha_path.exists() else ""
    ok = checksum == manifest.get("sha256") == sidecar and rows == manifest.get("rows")
    return {
        "model": model_key,
        "month": month,
        "ok": ok,
        "rows": rows,
        "expected_rows": manifest.get("rows"),
        "sha256": checksum,
        "expected_sha256": manifest.get("sha256"),
    }


def read_restore_rows(model_key: str, month: str, directory=None, grep: str | None = None, limit: int | None = 200):
    """流式读取归档行（不落库）：``grep`` 为原始 JSON 行子串匹配，``limit`` 为 0/None 表示不限。"""
    gz_path, _sha_path, _manifest_path = _paths(model_key, month, directory)
    if not gz_path.exists():
        raise FileNotFoundError(f"归档不存在：{gz_path}")
    emitted = 0
    with gzip.open(gz_path, "rt", encoding="utf-8") as gz:
        for line in gz:
            if grep and grep not in line:
                continue
            yield json.loads(line)
            emitted += 1
            if limit and emitted >= limit:
                break


__all__ = [
    "ARCHIVE_MODEL_KEYS",
    "archive_expired",
    "archive_month",
    "archive_root",
    "archive_watermark",
    "archived_months",
    "list_archives",
    "model_for",
    "month_of",
    "next_month",
    "parse_month",
    "prune_archived",
    "read_restore_rows",
    "verify_archive",
]
