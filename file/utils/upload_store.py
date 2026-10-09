#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""上传落库内核：安全校验（扩展名 / 大小 / 配额）+ 去重 + 分类 + 存储。

自 `file/views/admin/file.py` 的 upload 端点抽出：聊天室附件等业务上传入口
必须复用同一套安全策略与存储管线（两处各自实现必然漂移）。

调用语义保持视图层原有的两阶段形态：

    sizes = check_upload_limits(user, file_objs)   # 任一不合规即抛 UploadError（整体拒绝）
    with transaction.atomic():                     # 统一落库：任一失败整体回滚
        for file_obj in file_objs:
            upload, dedup_hit = store_upload_file(user, file_obj)

配额（``FILE_STORAGE_QUOTA_MB`` / ``FILE_UPLOAD_COUNT_LIMIT``）与单文件大小上限
（``FILE_UPLOAD_SIZE``，个人配置行只能收紧）在 ``check_upload_limits`` 内累计判定。
"""

import hashlib
import os
import re
from typing import Any

from django.core.cache import cache
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _

from common.core.config import SysConfig, get_personal_config_data, get_personal_int_config
from file.models import UploadFile
from file.utils.file_audit import validate_upload_extension
from file.utils.upload_category import resolve_upload_category

# 上传失败业务码：1002 文件不合法 / 1003 超过大小上限 / 1004 配额或数量超限
INVALID_CODE = 1002
SIZE_EXCEEDED_CODE = 1003
QUOTA_EXCEEDED_CODE = 1004


class UploadError(Exception):
    """上传校验失败：携带业务码与可读文案（视图层归一为 ApiResponse）。"""

    def __init__(self, code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.code = code
        self.detail = detail


def get_upload_max_size(user_obj: Any) -> int:
    """单文件上传大小上限：系统级为天花板，真实个人行只能收紧（min 语义）。"""
    system_value: int = SysConfig.FILE_UPLOAD_SIZE
    personal_data = get_personal_config_data(user_obj, "FILE_UPLOAD_SIZE")
    if personal_data is not None and isinstance(personal_data.get("value"), int) and personal_data["value"] > 0:
        personal_value: int = personal_data["value"]
        return min(system_value, personal_value)
    return system_value


def get_user_quota_mb(user_obj: Any) -> int:
    """个人文件存储配额（MB）：个人行优先，未设置继承系统级（0 = 不限）。"""
    quota: int = get_personal_int_config(user_obj, "FILE_STORAGE_QUOTA_MB", SysConfig.FILE_STORAGE_QUOTA_MB)
    return quota


def get_user_count_limit(user_obj: Any) -> int:
    """个人上传文件数量上限：个人行优先，未设置继承系统级（0 = 不限）。"""
    limit: int = get_personal_int_config(user_obj, "FILE_UPLOAD_COUNT_LIMIT", SysConfig.FILE_UPLOAD_COUNT_LIMIT)
    return limit


def sanitize_filename(name: Any, max_length: int = 255) -> str:
    """清洗客户端文件名：去除路径部分、控制字符与首尾空白，并限制长度。

    客户端提交的文件名不可信：可能携带路径分隔符（伪造存储路径）或控制字符。
    """
    if not name:
        return str(_("Unnamed file"))
    # 同时处理 POSIX(/) 与 Windows(\) 分隔符，防止路径穿越
    base = os.path.basename(str(name).replace("\\", "/")).strip()
    base = re.sub(r"[\x00-\x1f\x7f]", "", base)
    if not base or base in (".", ".."):
        return str(_("Unnamed file"))
    return base[:max_length]


def file_md5(file_obj: Any) -> str:
    """计算上传文件的 md5（落盘前求值：命中去重时无需再写一份磁盘文件）。

    上传链路原先由 ``UploadFile.save()`` 读已落盘文件计算 md5；去重需要在落盘**之前**
    拿到内容指纹，故此处统一改为前置计算并显式入库（save() 见 md5sum 非空即跳过）。
    """
    digest = hashlib.md5()
    for chunk in file_obj.chunks():
        digest.update(chunk)
    return digest.hexdigest()


def find_dedup_source(creator: Any, md5sum: str) -> Any:
    """去重来源：同属主的既有活动上传件；跨用户不复用（避免越权复用他人文件的存储路径）。

    只认 ``is_upload=True`` 且未软删除的记录（回收站中的文件不参与复用），
    空 md5（异常文件）不复用。
    """
    if not md5sum:
        return None
    return UploadFile.objects.filter(creator=creator, md5sum=md5sum, is_upload=True).order_by("-created_time").first()


def invalidate_upload_stats_cache(user_pk: Any) -> None:
    """失效个人文件统计短缓存（键口径与 get_stats_cache_key 一致）。

    上传成功后立刻刷新页面时，10s 短缓存会返回旧的使用率，故主动失效。
    """
    cache.delete(f"magic_cache_response_UploadFileViewSet_stats_{user_pk}")


def check_upload_limits(user_obj: Any, file_objs: Any) -> list[int]:
    """批量前置校验：返回各文件字节大小；任一不合规抛 UploadError（不落盘）。

    配额与数量上限按本批累计判定（与逐文件提交的语义一致），使多文件上传
    「前面的已落库、后面的被拒」不可能发生。
    """
    max_size = get_upload_max_size(user_obj)
    quota_mb = get_user_quota_mb(user_obj) or 0
    count_limit = get_user_count_limit(user_obj) or 0
    owner_files = UploadFile.objects.filter(creator=user_obj)
    used_size = (owner_files.aggregate(size=Sum("filesize"))["size"] or 0) if quota_mb else 0
    used_count = owner_files.count() if count_limit else 0

    sizes: list[int] = []
    for file_obj in file_objs:
        # 上传安全策略：扩展名黑名单（默认拒绝可执行 / 脚本类）+ 可选白名单，fail-closed
        extension_error = validate_upload_extension(file_obj.name)
        if extension_error:
            raise UploadError(INVALID_CODE, extension_error)
        try:
            file_size = file_obj.size
        except Exception as e:  # noqa: BLE001 读取大小失败（非文件对象）按不合法处理
            raise UploadError(INVALID_CODE, _("Wrong upload file type")) from e
        if file_size > max_size:
            raise UploadError(SIZE_EXCEEDED_CODE, _("upload file size cannot exceed {}").format(max_size))
        if quota_mb and used_size + file_size > quota_mb * 1024 * 1024:
            raise UploadError(
                QUOTA_EXCEEDED_CODE,
                _("Storage quota exceeded ({} MB), please clean up and retry").format(quota_mb),
            )
        if count_limit and used_count + 1 > count_limit:
            raise UploadError(
                QUOTA_EXCEEDED_CODE,
                _("File count limit exceeded ({}), please clean up and retry").format(count_limit),
            )
        used_size += file_size
        used_count += 1
        sizes.append(file_size)
    return sizes


def store_upload_file(user_obj: Any, file_obj: Any, *, is_tmp: bool = True, md5sum: str = "") -> tuple[Any, bool]:
    """单文件落库：md5 前置计算 → 同属主去重（复用物理文件）→ 分类 → 写记录。

    返回 ``(upload, dedup_hit)``；调用方负责事务边界与 stats 缓存失效。
    分片上传的 complete 路径已在合并期算出 md5（``md5sum`` 传入），避免整文件
    二次全量读；缺省仍由本函数从内容求值。
    """
    filename = sanitize_filename(file_obj.name)
    # md5 在落盘前求值：命中去重时不能再写一份磁盘文件
    md5sum = md5sum or file_md5(file_obj)
    # 自动分类：按 MIME/扩展名推断，且只写字典中存在的分类值
    # （去重命中与正常落盘共用同一结果，避免两条路径分类不一致）
    category = resolve_upload_category(filename, file_obj.content_type)
    source = find_dedup_source(user_obj, md5sum)
    if source:
        # 去重命中：复用既有物理文件，仅新建引用记录（不落盘）。
        # 归属语义不受影响（新记录仍是本次属主的上传件），删除任一记录时
        # 物理文件由 UploadFile.file_still_referenced 守护保留
        upload = UploadFile.objects.create(
            creator=user_obj,
            filename=filename,
            is_upload=True,
            is_tmp=is_tmp,
            filepath=source.filepath.name,
            mime_type=file_obj.content_type,
            filesize=file_obj.size,
            md5sum=md5sum,
            category=category,
        )
        return upload, True
    upload = UploadFile.objects.create(
        creator=user_obj,
        # 客户端原始文件名不可信：去掉路径部分并做非法字符/长度清洗
        filename=filename,
        is_upload=True,
        is_tmp=is_tmp,
        filepath=file_obj,
        mime_type=file_obj.content_type,
        filesize=file_obj.size,
        md5sum=md5sum,
        category=category,
    )
    return upload, False
