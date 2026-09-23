#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : media
# author : ly_13
# date : 1/17/2024
import mimetypes
import os
import posixpath
from pathlib import Path

from django.apps import apps
from django.http import FileResponse, Http404, HttpResponseNotModified
from django.utils._os import safe_join
from django.utils.http import http_date
from django.utils.translation import gettext_lazy as _
from django.views.static import directory_index, was_modified_since

from common.fields.image import ProcessedImageField, get_thumbnail


def get_media_path(path):
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


def _storage_serve(request, path):
    """对象存储后端（P-4）的媒体兜底：本地无文件时从存储读取并由应用层代理返回。"""
    from common.storage import storage_exists, storage_is_local, storage_open

    if storage_is_local() or not storage_exists(path):
        raise Http404(_("“%(path)s” does not exist") % {"path": path})
    content_type, encoding = mimetypes.guess_type(path)
    content_type = content_type or "application/octet-stream"
    response = FileResponse(storage_open(path, "rb"), content_type=content_type)
    if encoding:
        response.headers["Content-Encoding"] = encoding
    return response


def media_serve(request, path, document_root=None, show_indexes=False):
    path = posixpath.normpath(path).lstrip("/")
    fullpath = Path(safe_join(document_root, path))
    if fullpath.is_dir():
        if show_indexes:
            return directory_index(path, fullpath)
        raise Http404(_("Directory indexes are not allowed here."))
    if not fullpath.exists():
        media_path = get_media_path(path)
        if media_path:
            fullpath = Path(safe_join(document_root, media_path))
        else:
            # 对象存储后端：本地目录无该文件时回落到存储读取（远端内容应用层代理）
            return _storage_serve(request, path)
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
