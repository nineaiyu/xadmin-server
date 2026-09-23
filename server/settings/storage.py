#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""存储后端装配（自 base.py 拆出，控制单文件体量）。

``STORAGES["default"]`` 指向可插拔后端 ``common.storage.SwitchableStorage``：
默认 local（行为同 ``FileSystemStorage``），切对象存储走 SysConfig
（``FILE_STORAGE_BACKEND=s3`` + ``FILE_S3_*``）**运行期热生效**，无需改本文件；
``django-storages`` / ``boto3`` 为可选依赖，未安装 / 配置不全时自动回退本地并告警。
``MEDIA_URL`` / ``MEDIA_ROOT`` 仍在 base.py。
"""

STORAGES = {
    "default": {"BACKEND": "common.storage.SwitchableStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
