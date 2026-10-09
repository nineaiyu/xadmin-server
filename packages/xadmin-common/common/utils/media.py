#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : media
# author : ly_13
# date : 1/17/2024
"""媒体文件服务（受鉴权）。

口径：
- **不再有匿名直链**：nginx 侧的 `/media/` 静态直出已移除，请求统一经本视图
  鉴权（Cookie JWT：浏览器同源 img/link 请求；或 Django session）；
- 生产 nginx 部署可开启 `MEDIA_X_ACCEL_PREFIX`（如 `/_protected_media`）：
  鉴权通过后返回 `X-Accel-Redirect` 内部重定向，由 nginx 直出文件（零拷贝）；
  该内部位置必须声明 `internal`（不可外部寻址）；
- 细粒度文件授权（数据权限 + 访问审计）仍由受鉴权 download / preview 端点承担，
  本视图的鉴权粒度是「登录态」。
"""

import mimetypes
import os
import posixpath
from pathlib import Path
from typing import Any

from django.apps import apps
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, HttpResponseNotModified
from django.utils._os import safe_join
from django.utils.http import http_date
from django.utils.translation import gettext_lazy as _
from django.views.static import directory_index, was_modified_since

from common.fields.image import ProcessedImageField, get_thumbnail
from common.settings_contract import kernel_setting


def get_media_path(path: str) -> Any:
    path_list = path.split("/")
    if len(path_list) == 5:
        pic_names = path_list[4].split("_")
        if len(pic_names) != 2:
            return
        model = apps.get_model(path_list[0], path_list[1])
        field = None
        for i in model._meta.fields:
            if isinstance(i, ProcessedImageField):
                field = i
                break
        if field:
            pk = path_list[3]
            fw = {"pk": pk}
            if pk == "0":  # 通过form-data增加数据的时候，由于instance还未创建，pk不存在,为默认0
                fw = {field.name: path.replace(f"_{pic_names[1]}", f".{field.format}")}
            obj = model.objects.filter(**fw).first()
            if obj:
                pic = getattr(obj, field.name)
                if os.path.isfile(pic.path):
                    index = pic_names[1].split(".")
                    if pic and len(index) > 0:
                        return get_thumbnail(pic, int(index[0]))


def _storage_serve(request: Any, path: str) -> Any:
    """对象存储后端的媒体兜底：本地无文件时从存储读取并由应用层代理返回。"""
    from common.storage import storage_exists, storage_is_local, storage_open

    if storage_is_local() or not storage_exists(path):
        raise Http404(_("“%(path)s” does not exist") % {"path": path})
    content_type, encoding = mimetypes.guess_type(path)
    content_type = content_type or "application/octet-stream"
    response = FileResponse(storage_open(path, "rb"), content_type=content_type)
    if encoding:
        response.headers["Content-Encoding"] = encoding
    return response


def _is_authenticated(request: Any) -> bool:
    """媒体请求鉴权：Cookie JWT（浏览器同源请求）或已建立的 Django session。"""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return True
    try:
        from common.core.auth import CookieJWTAuthentication

        result = CookieJWTAuthentication().authenticate(request)
    except Exception:  # noqa: BLE001 凭证无效/会话失效一律按未认证处理
        return False
    if not result:
        return False
    request.user = result[0]
    return True


def media_serve(request: Any, path: str, document_root: Any = None, show_indexes: bool = False) -> Any:
    """受鉴权媒体服务：鉴权 → （可选）X-Accel 内转 → 本进程输出。"""
    if not _is_authenticated(request):
        return HttpResponseForbidden()

    path = posixpath.normpath(path).lstrip("/")
    fullpath = Path(safe_join(document_root, path))
    if fullpath.is_dir():
        if show_indexes:
            return directory_index(path, fullpath)
        raise Http404(_("Directory indexes are not allowed here."))
    relative_path = path
    if not fullpath.exists():
        media_path = get_media_path(path)
        if media_path:
            relative_path = media_path
            fullpath = Path(safe_join(document_root, media_path))
        else:
            # 对象存储后端：本地目录无该文件时回落到存储读取（远端内容应用层代理）
            return _storage_serve(request, path)

    accel_prefix = str(kernel_setting("MEDIA_X_ACCEL_PREFIX") or "").strip().rstrip("/")
    if accel_prefix and not kernel_setting("DEBUG"):
        # 生产 nginx：内部重定向给 nginx 直出（零拷贝）；内部位置声明 internal，
        # 外部不可寻址——鉴权已在上方完成。DEBUG（开发/E2E 直连）走下面的本进程输出
        content_type, encoding = mimetypes.guess_type(str(fullpath))
        response = HttpResponse(content_type=content_type or "application/octet-stream")
        if encoding:
            response.headers["Content-Encoding"] = encoding
        response["X-Accel-Redirect"] = f"{accel_prefix}/{relative_path}"
        return response

    # Respect the If-Modified-Since header.
    statobj = fullpath.stat()
    if not was_modified_since(request.META.get("HTTP_IF_MODIFIED_SINCE"), statobj.st_mtime):
        return HttpResponseNotModified()
    content_type, encoding = mimetypes.guess_type(str(fullpath))
    content_type = content_type or "application/octet-stream"
    response = FileResponse(fullpath.open("rb"), content_type=content_type)
    response.headers["Last-Modified"] = http_date(statobj.st_mtime)
    if encoding:
        response.headers["Content-Encoding"] = encoding
    return response
