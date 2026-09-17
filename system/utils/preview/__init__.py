# -*- coding: utf-8 -*-
"""文件在线预览：类型分派 + 缩略图按需生成（缓存可回收）+ 文本截断读取 + Office 转 PDF。

**落盘纪律**（四期 F2 教训 + 本期 P0-2）：凡是往磁盘落东西的功能，必须同时定义
清理与守护。预览缓存是**派生产物**，因此它有三条回收路径，缺一不可：

1. **源文件删除 → 联动清理**：`remove_preview_cache(upload)`（源文件删除时调用）；
2. **孤儿清理**：缓存目录里已查不到源记录的条目（`clean_preview_cache`）；
3. **保留期清理**：`FILE_PREVIEW_CACHE_KEEP_DAYS` 天未访问的条目（按需可重建）。

缓存路径与源文件**一对一可推导**（`preview_cache/<pk>/<size>.jpg`），
因此清理不需要额外索引表；并发生成用 cache 锁 + 原子替换，避免半截文件被读到。

**Office 在线预览**：docx/xlsx/pptx 等由 LibreOffice headless 转
PDF 后走既有 PDF 内嵌渲染。转换不在请求线程里做（耗时且吃 CPU），而是投递
`heavy` 队列任务 `system.tasks.convert_office_preview_task`，请求侧短等
（`FILE_OFFICE_WAIT_SECONDS`）产物流盘；未等到则返回业务码 1006，前端重试。
产物落 `preview_cache/<pk>/office.pdf`，与图片缓存同一目录 → 同一套清理/守护。

本包按职责拆分（constants / media / office），对外 API 由本文件统一再导出，
导入路径保持 ``system.utils.preview`` 不变。
"""

from .constants import (
    CONVERTER_CANDIDATES as CONVERTER_CANDIDATES,  # noqa: PLC0414 测试按模块属性访问
)
from .constants import (
    KIND_IMAGE,
    KIND_OFFICE,
    KIND_PDF,
    KIND_TEXT,
    PREVIEW_STATUS_PREPARING,
    PREVIEW_STATUS_READY,
    PREVIEW_STATUS_UNSUPPORTED,
    SIZE_PREVIEW,
    SIZE_THUMB,
)
from .media import (
    _generate_jpeg as _generate_jpeg,  # noqa: PLC0414 测试按模块属性访问（缩略图桩替换）
)
from .media import (
    clean_preview_cache,
    ensure_image_cache,
    preview_cache_dir,
    preview_cache_path,
    preview_kind,
    preview_kind_of,
    read_text_preview,
    remove_preview_cache,
    remove_preview_cache_by_pk,
    source_path,
    touch_preview_cache,
)
from .office import (
    convert_office_to_pdf,
    ensure_office_pdf,
    office_cache_path,
    office_converter_bin,
    office_preview_available,
)

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
    "preview_kind_of",
    "read_text_preview",
    "remove_preview_cache",
    "remove_preview_cache_by_pk",
    "source_path",
    "touch_preview_cache",
]
