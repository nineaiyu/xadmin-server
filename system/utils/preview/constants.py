# -*- coding: utf-8 -*-
"""文件在线预览：常量与惰性配置读取。"""

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
