# -*- coding: utf-8 -*-
"""文件在线预览：Office → PDF 转换链（heavy 队列执行 + 请求侧短等）。"""

import os
import shutil
import subprocess
import tempfile
import time
import uuid

from django.core.cache import cache

from common.utils import get_logger

from .constants import (
    CONVERTER_CANDIDATES,
    OFFICE_CACHE_FILENAME,
    OFFICE_CONVERT_LOCK_TIMEOUT,
    PREVIEW_STATUS_PREPARING,
    PREVIEW_STATUS_READY,
    PREVIEW_STATUS_UNSUPPORTED,
    _config,
    _config_value,
)
from .media import preview_cache_dir, source_path

logger = get_logger(__name__)


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
