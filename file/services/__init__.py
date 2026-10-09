#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""file app（文件域）对外服务契约层。

其他 app 需要使用 file 域业务能力时，只允许从本模块导入，禁止直接 import
file.models / file.serializers / file.utils 等内部实现，避免 app 间横向依赖
扩散（与 identity.services / system.services 同一口径）。

覆盖面：
- 模型契约：跨 app 关联（isinstance 判断、related field queryset、serializer
  Meta.model 等）统一经本门面引用；
- 服务函数：个人文件统计 / 在线预览状态机（``file_impl``）、过期文件与会话清理；
- 上传内核与预览工具的受控再导出：聊天附件等跨域消费方复用同一套上传 /
  预览管线（扩展名白名单、配额、分类判定、缓存触达），不出现第二套口径。

惰性导出说明：经 PEP 562 ``__getattr__`` 按需加载并缓存到模块 globals，
``from file.services import UploadFile`` 这类 from-import 仍然可用；本模块
自身保持零重导入，避免循环导入。
"""

from typing import Any

# 惰性导出名经 PEP 562 __getattr__ 提供，静态分析不可见，统一 noqa F822
__all__ = [
    # 模型契约
    "UploadFile",  # noqa: F822
    "UploadSession",  # noqa: F822
    "UploadSessionPart",  # noqa: F822
    "FileAccessLog",  # noqa: F822
    # 服务函数（文件统计 / 预览状态机，视图层与跨域消费方共用）
    "build_personal_file_stats",  # noqa: F822
    "resolve_preview",  # noqa: F822
    # 过期清理实现体（任务函数壳留在 system.tasks：celery 任务名 = 模块 + 函数名）
    "auto_clean_tmp_file",
    "auto_clean_upload_file",
    "auto_clean_preview_cache",
    "auto_clean_upload_sessions",
    # 上传内核（受控再导出：跨域消费方复用同一管线）
    "UploadError",  # noqa: F822
    "INVALID_CODE",  # noqa: F822
    "store_upload_file",  # noqa: F822
    "check_upload_limits",  # noqa: F822
    "get_user_quota_mb",  # noqa: F822
    "invalidate_upload_stats_cache",  # noqa: F822
    # 上传分类（受控再导出）
    "CATEGORY_AUDIO",  # noqa: F822
    "CATEGORY_VIDEO",  # noqa: F822
    "UPLOAD_CATEGORY_DICT",  # noqa: F822
    "guess_upload_category",  # noqa: F822
    # 文件访问审计（受控再导出）
    "log_file_access",  # noqa: F822
    # 预览工具（受控再导出）
    "KIND_IMAGE",  # noqa: F822
    "KIND_OFFICE",  # noqa: F822
    "KIND_PDF",  # noqa: F822
    "KIND_TEXT",  # noqa: F822
    "PREVIEW_STATUS_PREPARING",  # noqa: F822
    "PREVIEW_STATUS_READY",  # noqa: F822
    "SIZE_THUMB",  # noqa: F822
    "SIZE_PREVIEW",  # noqa: F822
    "ensure_image_cache",  # noqa: F822
    "ensure_office_pdf",  # noqa: F822
    "read_text_preview",  # noqa: F822
    "touch_preview_cache",  # noqa: F822
    "preview_kind",  # noqa: F822
    "clean_preview_cache",  # noqa: F822
    "remove_preview_cache_by_pk",  # noqa: F822
]

# 惰性再导出表：名字 -> 所属模块
_LAZY_EXPORTS = {
    "UploadFile": "file.models",
    "UploadSession": "file.models",
    "UploadSessionPart": "file.models",
    "FileAccessLog": "file.models",
    # 服务函数（预览状态机与统计聚合实现体）
    "build_personal_file_stats": "file.services.file_impl",
    "resolve_preview": "file.services.file_impl",
    # 上传内核 / 分类 / 审计
    "UploadError": "file.utils.upload_store",
    "INVALID_CODE": "file.utils.upload_store",
    "store_upload_file": "file.utils.upload_store",
    "check_upload_limits": "file.utils.upload_store",
    "get_user_quota_mb": "file.utils.upload_store",
    "invalidate_upload_stats_cache": "file.utils.upload_store",
    "CATEGORY_AUDIO": "file.utils.upload_category",
    "CATEGORY_VIDEO": "file.utils.upload_category",
    "UPLOAD_CATEGORY_DICT": "file.utils.upload_category",
    "guess_upload_category": "file.utils.upload_category",
    "log_file_access": "file.utils.file_audit",
    # 预览工具
    "KIND_IMAGE": "file.utils.preview",
    "KIND_OFFICE": "file.utils.preview",
    "KIND_PDF": "file.utils.preview",
    "KIND_TEXT": "file.utils.preview",
    "PREVIEW_STATUS_PREPARING": "file.utils.preview",
    "PREVIEW_STATUS_READY": "file.utils.preview",
    "SIZE_THUMB": "file.utils.preview",
    "SIZE_PREVIEW": "file.utils.preview",
    "ensure_image_cache": "file.utils.preview",
    "ensure_office_pdf": "file.utils.preview",
    "read_text_preview": "file.utils.preview",
    "touch_preview_cache": "file.utils.preview",
    "preview_kind": "file.utils.preview",
    "clean_preview_cache": "file.utils.preview",
    "remove_preview_cache_by_pk": "file.utils.preview",
    # 清理实现体
    "auto_clean_upload_sessions": "file.utils.upload_chunk",
    "auto_clean_tmp_file": "file.services.cleanup",
    "auto_clean_upload_file": "file.services.cleanup",
    "auto_clean_preview_cache": "file.services.cleanup",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is not None:
        from importlib import import_module

        value = getattr(import_module(module_path), name)
        globals()[name] = value  # 首次访问后缓存为模块属性
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
