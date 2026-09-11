# -*- coding: utf-8 -*-
"""文件在线预览：类型分派 + 缩略图按需生成（缓存可回收）+ 文本截断读取 + Office 转 PDF。

**落盘纪律**（四期 F2 教训 + 本期 P0-2）：凡是往磁盘落东西的功能，必须同时定义
清理与守护。预览缓存是**派生产物**，因此它有三条回收路径，缺一不可：

1. **源文件删除 → 联动清理**：`remove_preview_cache(upload)`（源文件删除时调用）；
2. **孤儿清理**：缓存目录里已查不到源记录的条目（`clean_preview_cache`）；
3. **保留期清理**：`FILE_PREVIEW_CACHE_KEEP_DAYS` 天未访问的条目（按需可重建）。

缓存路径与源文件**一对一可推导**（`preview_cache/<pk>/<size>.jpg`），
因此清理不需要额外索引表；并发生成用 cache 锁 + 原子替换，避免半截文件被读到。

**Office 在线预览（ADR-013）**：docx/xlsx/pptx 等由 LibreOffice headless 转
PDF 后走既有 PDF 内嵌渲染。转换不在请求线程里做（耗时且吃 CPU），而是投递
`heavy` 队列任务 `system.tasks.convert_office_preview_task`，请求侧短等
（`FILE_OFFICE_WAIT_SECONDS`）产物流盘；未等到则返回业务码 1006，前端重试。
产物落 `preview_cache/<pk>/office.pdf`，与图片缓存同一目录 → 同一套清理/守护。
"""

import os
import shutil
import subprocess
import tempfile
import time
import uuid

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from common.utils import get_logger

logger = get_logger(__name__)

__all__ = [
    "KIND_IMAGE",
    "KIND_OFFICE",
    "KIND_PDF",
    "KIND_TEXT",
    "PREVIEW_STATUS_PREPARING",
    "PREVIEW_STATUS_READY",
    "PREVIEW_STATUS_UNSUPPORTED",
    "SIZE_PREVIEW",
    "SIZE_THUMB",
    "clean_preview_cache",
    "convert_office_to_pdf",
    "ensure_image_cache",
    "ensure_office_pdf",
    "office_cache_path",
    "office_converter_bin",
    "office_preview_available",
    "preview_cache_dir",
    "preview_cache_path",
    "preview_kind",
    "read_text_preview",
    "remove_preview_cache",
    "remove_preview_cache_by_pk",
    "touch_preview_cache",
]

KIND_IMAGE = "image"
KIND_PDF = "pdf"
KIND_TEXT = "text"
KIND_OFFICE = "office"

# Office 转换状态（视图据此返回 ready/1006/1005）
PREVIEW_STATUS_READY = "ready"
PREVIEW_STATUS_PREPARING = "preparing"
PREVIEW_STATUS_UNSUPPORTED = "unsupported"

# Office 系后缀 / MIME（判定优先级低于 image/pdf/text：csv 等仍按文本预览）
OFFICE_EXTENSIONS = (
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
)
OFFICE_MIME_TYPES = (
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "application/rtf",
)

# LibreOffice 可执行文件探测顺序：SysConfig 指定 → PATH → 常见安装路径
CONVERTER_CANDIDATES = (
    "soffice",
    "libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "C:\\Program Files\\LibreOffice\\program\\soffice.exe",
)
# 转换产物文件名（与图片缓存在同一 pk 目录：thumb.jpg / preview.jpg / office.pdf）
OFFICE_CACHE_FILENAME = "office.pdf"
# 转换锁：同一文件只有第一个请求触发转换，其余等待产物（任务结束时释放）
OFFICE_CONVERT_LOCK_TIMEOUT = 300

#: 图片预览的两档尺寸（键 = 接口 `size` 参数值）
SIZE_THUMB = "thumb"
SIZE_PREVIEW = "preview"

# 缓存目录名（位于 MEDIA_ROOT 下，与业务上传目录隔离，便于整体清理）
CACHE_DIR_NAME = "preview_cache"

# 文本预览：除 MIME 前缀外，这些后缀同样按文本处理（日志/配置/表格另存）
TEXT_EXTENSIONS = (
    ".txt",
    ".log",
    ".csv",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".ini",
    ".conf",
    ".md",
    ".sql",
    ".py",
    ".js",
    ".ts",
    ".vue",
    ".sh",
)
TEXT_MIME_EXACT = (
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/javascript",
)

# 生成锁：避免同一文件同一尺寸被并发请求重复生成（缓存穿透时的惊群）
GENERATE_LOCK_TIMEOUT = 30
GENERATE_WAIT_SECONDS = 2.0


def _config(key, default):
    """惰性读 SysConfig（int）：避免模块导入期触发配置表查询（迁移/命令场景）。"""
    value = _config_value(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _config_value(key, default):
    """惰性读 SysConfig（原值）：未登记（None）时回退默认值。

    注意不能直接用 `getattr(SysConfig, key, default)`：SysConfig 的魔法回退
    对未登记键返回 None（不抛 AttributeError），getattr 的默认值不会生效。
    """
    from common.core.config import SysConfig

    value = getattr(SysConfig, key, None)
    return default if value is None else value


def preview_kind(upload) -> str | None:
    """判定预览类型；`None` 表示不支持预览（前端应只给下载入口）。

    Office 判定排在文本之后：csv 等"表格类文本"仍按文本预览（体验更好且零转换成本）。
    """
    mime = (getattr(upload, "mime_type", "") or "").lower()
    filename = (getattr(upload, "filename", "") or "").lower()
    if mime.startswith("image/"):
        return KIND_IMAGE
    if mime == "application/pdf" or filename.endswith(".pdf"):
        return KIND_PDF
    if mime.startswith("text/") or mime in TEXT_MIME_EXACT:
        return KIND_TEXT
    if filename.endswith(TEXT_EXTENSIONS):
        return KIND_TEXT
    if mime in OFFICE_MIME_TYPES or filename.endswith(OFFICE_EXTENSIONS):
        return KIND_OFFICE
    return None


def preview_cache_dir() -> str:
    return os.path.join(str(settings.MEDIA_ROOT), CACHE_DIR_NAME)


def preview_cache_path(upload, size: str = SIZE_THUMB) -> str:
    """缓存路径：`preview_cache/<pk>/<size>.jpg`（与源文件一对一可推导）。"""
    return os.path.join(preview_cache_dir(), str(upload.pk), f"{size}.jpg")


def source_path(upload) -> str | None:
    """源文件在磁盘上的绝对路径；缺失或不存在返回 None。"""
    filepath = getattr(upload, "filepath", None)
    if not filepath:
        return None
    path = filepath.path
    return path if os.path.exists(path) else None


def _width_for(size: str) -> int:
    if size == SIZE_PREVIEW:
        return _config("FILE_PREVIEW_IMAGE_WIDTH", 1280)
    return _config("FILE_PREVIEW_THUMB_WIDTH", 240)


def ensure_image_cache(upload, size: str = SIZE_THUMB) -> str | None:
    """按需生成图片预览缓存并返回缓存文件路径（已存在则直接返回）。

    并发去重：同一 (pk, size) 只有第一个请求生成，其余短等其产出；
    写入用临时文件 + `os.replace` 原子替换，避免读到半截文件。
    """
    if size not in (SIZE_THUMB, SIZE_PREVIEW):
        return None
    source = source_path(upload)
    if not source:
        return None

    target = preview_cache_path(upload, size)
    if os.path.exists(target):
        return target

    lock_key = f"preview_generating_{upload.pk}_{size}"
    if not cache.add(lock_key, "1", timeout=GENERATE_LOCK_TIMEOUT):
        # 已有请求在生成：短等其落盘，超时后自行生成（不阻塞用户）
        deadline = time.monotonic() + GENERATE_WAIT_SECONDS
        while time.monotonic() < deadline:
            if os.path.exists(target):
                return target
            time.sleep(0.1)

    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp_path = f"{target}.{uuid.uuid4().hex}.tmp"
        _generate_jpeg(source, tmp_path, _width_for(size))
        os.replace(tmp_path, target)
        return target
    except Exception as exc:  # noqa: BLE001 生成失败不影响下载，只记录
        logger.warning(f"generate preview cache failed. file:{upload.pk} size:{size} error:{exc}")
        return None
    finally:
        cache.delete(lock_key)


def _generate_jpeg(source_path_: str, target_path: str, width: int) -> None:
    """等比缩放到目标宽度并写成 JPEG（统一格式，避免 PNG/GIF 体积失控）。"""
    from PIL import Image, ImageOps

    with Image.open(source_path_) as img:
        # 手机/相机照片的方向信息在 EXIF 里，不纠正会出现"躺着"的缩略图
        img = ImageOps.exif_transpose(img)
        if img.width > width:
            img.thumbnail((width, width))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(target_path, "JPEG", quality=85)


def read_text_preview(upload, max_bytes: int | None = None) -> tuple[str, bool]:
    """读取文本预览内容：`(内容, 是否截断)`。

    二进制文件（含 NUL 字节）返回空内容 + 未截断，由调用方按「不支持预览」处理。
    """
    if max_bytes is None:
        max_bytes = _config("FILE_PREVIEW_TEXT_MAX_BYTES", 256 * 1024)
    source = source_path(upload)
    if not source:
        return "", False
    try:
        with open(source, "rb") as file:
            raw = file.read(max_bytes + 1)
    except OSError as exc:
        logger.warning(f"read text preview failed. file:{upload.pk} error:{exc}")
        return "", False

    if b"\x00" in raw[:4096]:
        return "", False
    truncated = len(raw) > max_bytes
    return raw[:max_bytes].decode("utf-8", errors="replace"), truncated


def touch_preview_cache(path: str) -> None:
    """命中缓存时刷新 mtime：保留期清理按「最近使用」淘汰，常用预览不会被误删。"""
    try:
        os.utime(path, None)
    except OSError:
        pass


# ---------------------------------------------------------------- Office → PDF


def office_converter_bin() -> str | None:
    """定位 LibreOffice 可执行文件：SysConfig 指定 → PATH → 常见安装路径。

    未安装返回 None（预览按「不支持」降级，不影响下载与其余类型预览）。
    """
    configured = str(_config_value("FILE_OFFICE_SOFFICE_BIN", "") or "").strip()
    candidates = ([configured] if configured else []) + list(CONVERTER_CANDIDATES)
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isabs(candidate):
            if os.path.exists(candidate) and os.access(candidate, os.X_OK):
                return candidate
            continue
        found = shutil.which(candidate)
        if found:
            return found
    return None


def office_cache_path(upload) -> str:
    """Office 转 PDF 产物路径：`preview_cache/<pk>/office.pdf`（与图片缓存同目录）。"""
    return os.path.join(preview_cache_dir(), str(upload.pk), OFFICE_CACHE_FILENAME)


def office_preview_available(upload) -> bool:
    """Office 预览可用性：开关开、转换器已安装、源文件在且不超过大小上限。"""
    if not bool(_config_value("FILE_OFFICE_PREVIEW_ENABLED", True)):
        return False
    if office_converter_bin() is None:
        return False
    source = source_path(upload)
    if not source:
        return False
    max_bytes = _config("FILE_OFFICE_MAX_BYTES", 20 * 1024 * 1024)
    if max_bytes and os.path.getsize(source) > max_bytes:
        return False
    return True


def convert_office_to_pdf(upload) -> str | None:
    """同步执行 LibreOffice 转换（**必须在 heavy 队列任务内调用**）：成功返回产物路径。

    - 源文件复制到临时目录并使用安全文件名（含原后缀，soffice 依赖后缀识别格式）；
    - 独立 `-env:UserInstallation` 用户配置目录：避免多进程并发时 profile 锁冲突；
    - `subprocess.run(timeout=FILE_OFFICE_CONVERT_TIMEOUT)` 硬超时，超时杀进程；
    - 产物用 `os.replace` 原子落盘，避免半截 PDF 被请求侧读到。
    """
    converter = office_converter_bin()
    source = source_path(upload)
    if not converter or not source:
        return None
    timeout = _config("FILE_OFFICE_CONVERT_TIMEOUT", 60)
    extension = os.path.splitext(source)[1] or ".bin"
    target = office_cache_path(upload)
    workdir = tempfile.mkdtemp(prefix="office_preview_")
    try:
        local_source = os.path.join(workdir, f"source{extension}")
        shutil.copyfile(source, local_source)
        profile_dir = os.path.join(workdir, "profile")
        outdir = os.path.join(workdir, "out")
        os.makedirs(outdir, exist_ok=True)
        command = [
            converter,
            "--headless",
            "--norestore",
            "--invisible",
            f"-env:UserInstallation=file://{profile_dir}",
            "--convert-to",
            "pdf",
            "--outdir",
            outdir,
            local_source,
        ]
        completed = subprocess.run(  # noqa: S603 参数为列表且路径来自受控配置
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        produced = os.path.join(outdir, f"source{os.path.splitext(extension)[0]}.pdf")
        if not os.path.exists(produced):
            # 部分版本按 basename 直出：兜底扫描输出目录里的第一个 pdf
            pdfs = [name for name in os.listdir(outdir) if name.lower().endswith(".pdf")]
            produced = os.path.join(outdir, pdfs[0]) if pdfs else ""
        if not produced or not os.path.exists(produced):
            logger.warning(
                "convert office to pdf failed. file:%s returncode:%s stderr:%s",
                upload.pk,
                completed.returncode,
                (completed.stderr or b"")[:500],
            )
            return None
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp_target = f"{target}.{uuid.uuid4().hex}.tmp"
        shutil.move(produced, tmp_target)
        os.replace(tmp_target, target)
        return target
    except subprocess.TimeoutExpired:
        logger.warning("convert office to pdf timeout. file:%s timeout:%s", upload.pk, timeout)
        return None
    except Exception as exc:  # noqa: BLE001 转换失败降级为不可预览，不影响下载
        logger.warning("convert office to pdf error. file:%s error:%s", upload.pk, exc)
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def ensure_office_pdf(upload) -> tuple[str | None, str]:
    """请求侧入口：返回 `(产物路径, 状态)`，状态 ∈ ready / preparing / unsupported。

    缓存命中直接返回；未命中则投递 heavy 队列转换任务并短等
    `FILE_OFFICE_WAIT_SECONDS`（eager 环境/已转换完成即刻命中）；仍在转换中返回
    preparing（视图回 1006，前端稍后重试）。
    """
    if not office_preview_available(upload):
        return None, PREVIEW_STATUS_UNSUPPORTED
    target = office_cache_path(upload)
    if os.path.exists(target):
        return target, PREVIEW_STATUS_READY

    lock_key = f"office_converting_{upload.pk}"
    if cache.add(lock_key, "1", timeout=OFFICE_CONVERT_LOCK_TIMEOUT):
        try:
            from system.tasks import convert_office_preview_task

            # task_id 用源文件 pk（UUID，与 TaskExecution 主键口径一致，便于按文件追溯）
            convert_office_preview_task.apply_async(args=[str(upload.pk)], task_id=str(upload.pk))
        except Exception:  # noqa: BLE001 投递失败不阻塞：释放锁，让后续请求重试
            cache.delete(lock_key)
            logger.warning("dispatch office convert task failed. file:%s", upload.pk, exc_info=True)

    wait_seconds = _config("FILE_OFFICE_WAIT_SECONDS", 8)
    deadline = time.monotonic() + max(wait_seconds, 0)
    while time.monotonic() < deadline:
        if os.path.exists(target):
            return target, PREVIEW_STATUS_READY
        time.sleep(0.2)

    # 等待窗口结束仍未就绪：锁被释放（任务已结束且无产物）= 转换失败 → 不支持预览；
    # 锁仍在（任务排队/转换中）→ 转换中，由前端稍后重试
    if not cache.get(lock_key):
        return None, PREVIEW_STATUS_UNSUPPORTED
    return None, PREVIEW_STATUS_PREPARING


def remove_preview_cache(upload) -> int:
    """删除某个源文件的全部预览缓存（源文件删除时联动），返回删除条数。"""
    return remove_preview_cache_by_pk(upload.pk)


def remove_preview_cache_by_pk(pk) -> int:
    """按主键删除预览缓存目录。

    独立成函数的原因：Django 的 `Model.delete()` 会把实例 `pk` 置为 None，
    而缓存目录按 pk 推导，删除后再取值会得到 `.../None/`（静默漏删）。
    """
    import shutil

    directory = os.path.join(preview_cache_dir(), str(pk))
    if not os.path.isdir(directory):
        return 0
    shutil.rmtree(directory, ignore_errors=True)
    return 1


def clean_preview_cache(keep_days: int | None = None, batch: int = 2000) -> dict:
    """清理预览缓存：孤儿（源记录已不存在）+ 超保留期（默认 FILE_PREVIEW_CACHE_KEEP_DAYS）。

    :return: `{"scanned": n, "removed_orphan": n, "removed_expired": n}`
    """
    from system.models import UploadFile

    if keep_days is None:
        keep_days = _config("FILE_PREVIEW_CACHE_KEEP_DAYS", 7)

    root = preview_cache_dir()
    if not os.path.isdir(root):
        return {"scanned": 0, "removed_orphan": 0, "removed_expired": 0}

    deadline = None
    if keep_days and keep_days > 0:
        deadline = time.time() - keep_days * 86400

    scanned = orphan = expired = 0
    for name in os.listdir(root)[:batch]:
        directory = os.path.join(root, name)
        if not os.path.isdir(directory):
            continue
        scanned += 1
        exists = UploadFile.all_objects.filter(pk=name).exists()
        if not exists:
            _remove_dir(directory)
            orphan += 1
            continue
        if deadline is not None and _dir_mtime(directory) < deadline:
            # 保留期内被访问过则刷新 mtime（预览命中时由调用方 touch）
            _remove_dir(directory)
            expired += 1
    logger.info(f"clean preview cache scanned:{scanned} orphan:{orphan} expired:{expired}")
    return {"scanned": scanned, "removed_orphan": orphan, "removed_expired": expired}


def _remove_dir(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _dir_mtime(path: str) -> float:
    try:
        return max(os.path.getmtime(os.path.join(root, file)) for root, _dirs, files in os.walk(path) for file in files)
    except (OSError, ValueError):
        return timezone.now().timestamp()
