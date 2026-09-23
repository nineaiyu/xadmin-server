#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""可插拔文件存储：``SwitchableStorage`` + 业务侧统一访问适配层。

- ``default_storage`` 经 ``STORAGES["default"]`` 指向 :class:`SwitchableStorage`；
- 业务代码统一使用本模块的 helpers（``storage_open`` / ``storage_local_path`` 等），
  不得直接使用 ``filepath.path``（对象存储无本地路径）。
"""

from common.storage.backend import (
    BACKEND_LOCAL,
    BACKEND_MIRROR,
    BACKEND_S3,
    MirrorStorage,
    SwitchableStorage,
    build_delegate,
    storage_config,
)
from common.storage.utils import (
    clean_storage_cache,
    storage_backend_name,
    storage_cache_dir,
    storage_exists,
    storage_is_local,
    storage_local_path,
    storage_open,
    storage_presigned_url,
    storage_probe,
    storage_size,
    storage_url,
)

__all__ = [
    "BACKEND_LOCAL",
    "BACKEND_MIRROR",
    "BACKEND_S3",
    "MirrorStorage",
    "SwitchableStorage",
    "build_delegate",
    "storage_config",
    "clean_storage_cache",
    "storage_backend_name",
    "storage_cache_dir",
    "storage_exists",
    "storage_is_local",
    "storage_local_path",
    "storage_open",
    "storage_presigned_url",
    "storage_probe",
    "storage_size",
    "storage_url",
]
