#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""可插拔文件存储后端：按 SysConfig 声明把文件操作委托给本地 / 对象存储。

设计要点：

- **默认零变化**：``FILE_STORAGE_BACKEND=local``（默认）委托
  ``FileSystemStorage(location=MEDIA_ROOT)``，与 Django 默认存储行为一致；
- **可选依赖零加载**：``s3`` / ``mirror`` 后端只在启用时导入 ``storages.backends.s3``
  （django-storages / boto3 为可选依赖：``pip install django-storages boto3``，
  或 ``uv sync --extra storage``）；未安装或配置不全时**回退本地并记录可诊断日志**
  （文件链路可用性优先）；
- **运行期可切换**：委托实例按「配置指纹」缓存，SysConfig 改动后下一次文件操作即生效；
- **搬迁窗口双写**：``mirror`` = 本地为主 + 对象存储尽力副本（新写入双写、读全走本地），
  配合 ``storage_migrate`` 补齐存量后切 ``s3``（全程无停服窗口）。

配置项（SysConfig，密钥经 signer 加密存储，见 ``common/core/credentials.py``）：

FILE_STORAGE_BACKEND / FILE_S3_ENDPOINT / FILE_S3_BUCKET / FILE_S3_ACCESS_KEY /
FILE_S3_SECRET_KEY / FILE_S3_REGION / FILE_S3_CUSTOM_DOMAIN / FILE_S3_ADDRESSING_STYLE
"""

import threading

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage

from common.utils import get_logger

logger = get_logger(__name__)

BACKEND_LOCAL = "local"
BACKEND_S3 = "s3"
BACKEND_MIRROR = "mirror"

#: 回退告警去重（同一原因只告警一次，避免每次文件操作刷日志）
_warned_reasons: set = set()
_warn_lock = threading.Lock()


def _warn_once(reason: str):
    with _warn_lock:
        if reason in _warned_reasons:
            return
        _warned_reasons.add(reason)
    logger.warning("storage backend fallback to local: %s", reason)


def storage_config() -> dict:
    """当前存储配置（SysConfig 声明式）。

    读配置失败（迁移期 / 库不可用）回退 local：文件链路可用性优先于切换语义。
    """
    config = {
        "backend": BACKEND_LOCAL,
        "endpoint": "",
        "bucket": "",
        "access_key": "",
        "secret_key": "",
        "region": "",
        "custom_domain": "",
        "addressing_style": "",
    }
    try:
        from common.core.config import SysConfig

        config.update(
            {
                "backend": str(SysConfig.FILE_STORAGE_BACKEND or BACKEND_LOCAL).strip().lower(),
                "endpoint": str(SysConfig.FILE_S3_ENDPOINT or "").strip(),
                "bucket": str(SysConfig.FILE_S3_BUCKET or "").strip(),
                "access_key": str(SysConfig.FILE_S3_ACCESS_KEY or "").strip(),
                "secret_key": str(SysConfig.FILE_S3_SECRET_KEY or "").strip(),
                "region": str(SysConfig.FILE_S3_REGION or "").strip(),
                "custom_domain": str(SysConfig.FILE_S3_CUSTOM_DOMAIN or "").strip(),
                "addressing_style": str(SysConfig.FILE_S3_ADDRESSING_STYLE or "").strip(),
            }
        )
    except Exception:  # noqa: BLE001 配置不可读时保持本地默认（不阻断文件链路）
        logger.warning("read storage config failed, fallback local", exc_info=True)
    return config


def config_fingerprint(config: dict) -> tuple:
    """配置指纹：用于判断委托实例是否需要重建（切换后端 / 换 bucket 等）。"""
    return tuple(sorted(config.items()))


def _local_storage() -> FileSystemStorage:
    return FileSystemStorage(location=str(settings.MEDIA_ROOT), base_url=str(settings.MEDIA_URL))


def build_delegate(config: dict) -> Storage:
    """按配置构建委托存储；s3 / mirror 不可用时回退本地。"""
    backend = config.get("backend")
    if backend == BACKEND_S3:
        delegate = _build_s3(config)
        if delegate is not None:
            return delegate
    elif backend == BACKEND_MIRROR:
        delegate = _build_mirror(config)
        if delegate is not None:
            return delegate
    return _local_storage()


def _build_s3(config: dict, file_overwrite: bool = False):
    """构建 S3 后端（django-storages 为可选依赖；缺失 / 配置不全返回 None）。

    ``file_overwrite`` 默认 False（同名不覆盖，与既有口径一致）；mirror 副本写入传 True
    —— 本地主存储已完成唯一命名，副本按同名覆盖以保证两侧对象名一致。
    """
    if not config.get("bucket"):
        _warn_once("FILE_S3_BUCKET 未配置")
        return None
    try:
        from storages.backends.s3 import S3Storage
    except ImportError:
        _warn_once("未安装 django-storages（pip install django-storages boto3）")
        return None
    options = {
        "bucket_name": config["bucket"],
        "access_key": config.get("access_key") or None,
        "secret_key": config.get("secret_key") or None,
        "region_name": config.get("region") or None,
        "endpoint_url": config.get("endpoint") or None,
        "addressing_style": config.get("addressing_style") or None,
        "custom_domain": config.get("custom_domain") or None,
        # 自定义域名（CDN / 公开桶）不签名；否则生成带签名 URL（私有桶必需）
        "querystring_auth": not bool(config.get("custom_domain")),
        "file_overwrite": file_overwrite,
    }
    try:
        return S3Storage(**{key: value for key, value in options.items() if value is not None})
    except Exception as e:  # noqa: BLE001 参数非法 / 依赖版本差异：回退本地
        _warn_once(f"S3 后端构建失败：{e}")
        return None


def _build_mirror(config: dict):
    """构建双写（搬迁窗口）后端：本地为主存储 + 对象存储尽力副本；副本不可用回退纯本地。"""
    replica = _build_s3(config, file_overwrite=True)
    if replica is None:
        _warn_once("mirror 模式缺少可用对象存储副本（依赖未装 / 配置不全），回退纯本地")
        return None
    return MirrorStorage(_local_storage(), replica)


class MirrorStorage(Storage):
    """搬迁窗口双写：本地为主存储，对象存储为尽力副本。

    语义（读全走本地，写入双写）：

    - 读 / 存在性 / 大小 / URL / 本地路径 / 目录列举一律委托本地——本地始终完整，
      行为与 ``local`` 一致（``storage_is_local()`` 判为真，缩略图等本地专属逻辑正常）；
    - 新写入文件同时落本地与远端（副本失败只告警，不影响上传成功）；
    - 删除两边尽力执行（副本失败只告警）；
    - 搬迁流程：切 ``mirror`` → ``storage_migrate`` 补齐存量 → 切 ``s3``（全程无停服窗口）。
    """

    #: 后端名（storage_backend_name() 诊断口径）
    backend_name = BACKEND_MIRROR

    def __init__(self, primary: Storage, replica: Storage):
        super().__init__()
        self.primary = primary
        self.replica = replica

    def _replica_call(self, action: str, name: str, func):
        """副本尽力语义：异常只告警（主链路可用性优先）。"""
        try:
            return func()
        except Exception:  # noqa: BLE001
            logger.warning("mirror %s replica failed. name:%s", action, name, exc_info=True)
            return None

    def save(self, name, content, max_length=None):
        saved = self.primary.save(name, content, max_length)

        def _copy():
            # 本地落盘后按同一对象名复制到远端（流式，不整份读进内存）
            with self.primary.open(saved, "rb") as source:
                return self.replica.save(saved, source, max_length)

        self._replica_call("write", saved, _copy)
        return saved

    def delete(self, name):
        result = self.primary.delete(name)
        self._replica_call("delete", name, lambda: self.replica.delete(name))
        return result

    # ---- Storage 接口显式委托（与 SwitchableStorage 同口径） ----
    def open(self, name, mode="rb"):
        return self.primary.open(name, mode)

    def exists(self, name):
        return self.primary.exists(name)

    def listdir(self, path):
        return self.primary.listdir(path)

    def size(self, name):
        return self.primary.size(name)

    def url(self, name):
        return self.primary.url(name)

    def path(self, name):
        return self.primary.path(name)

    def get_accessed_time(self, name):
        return self.primary.get_accessed_time(name)

    def get_created_time(self, name):
        return self.primary.get_created_time(name)

    def get_modified_time(self, name):
        return self.primary.get_modified_time(name)

    def get_valid_name(self, name):
        return self.primary.get_valid_name(name)

    def get_available_name(self, name, max_length=None):
        return self.primary.get_available_name(name, max_length)

    def get_alternative_name(self, file_root, file_ext):
        return self.primary.get_alternative_name(file_root, file_ext)

    def is_name_available(self, name, max_length=None):
        return self.primary.is_name_available(name, max_length)

    def generate_filename(self, filename):
        return self.primary.generate_filename(filename)


class SwitchableStorage(Storage):
    """声明式存储后端：运行期按 SysConfig 委托 local / s3 后端。

    ``Storage`` 基类的默认实现对本类不可用（其方法内部依赖具体后端的私有能力），
    故 Storage 接口逐项显式委托给当前生效的委托实例。
    """

    def __init__(self, location=None, base_url=None, **kwargs):
        super().__init__(**kwargs)
        self._lock = threading.Lock()
        self._fingerprint = None
        self._delegate: Storage | None = None

    def _current(self) -> Storage:
        config = storage_config()
        fingerprint = config_fingerprint(config)
        if self._delegate is not None and fingerprint == self._fingerprint:
            return self._delegate
        with self._lock:
            if self._delegate is None or fingerprint != self._fingerprint:
                self._delegate = build_delegate(config)
                self._fingerprint = fingerprint
            return self._delegate

    @property
    def delegate(self) -> Storage:
        """当前生效的委托存储（测试 / 诊断用）。"""
        return self._current()

    @property
    def is_local(self) -> bool:
        """当前生效后端是否「以本地文件系统为准」（链路分支的统一判据）。

        ``mirror``（双写）以本地为主存储（本地始终完整）→ 同样视为本地。
        """
        storage = self._current()
        return isinstance(storage, (FileSystemStorage, MirrorStorage))

    @property
    def backend_name(self) -> str:
        """当前生效后端名（local / s3 / mirror / 其它类名，诊断用）。"""
        storage = self._current()
        if isinstance(storage, FileSystemStorage):
            return BACKEND_LOCAL
        name = getattr(storage, "backend_name", None)
        return name if isinstance(name, str) else storage.__class__.__name__

    # ---- Storage 接口显式委托 ----
    def open(self, name, mode="rb"):
        return self._current().open(name, mode)

    def save(self, name, content, max_length=None):
        return self._current().save(name, content, max_length)

    def delete(self, name):
        return self._current().delete(name)

    def exists(self, name):
        return self._current().exists(name)

    def listdir(self, path):
        return self._current().listdir(path)

    def size(self, name):
        return self._current().size(name)

    def url(self, name):
        return self._current().url(name)

    def path(self, name):
        return self._current().path(name)

    def get_accessed_time(self, name):
        return self._current().get_accessed_time(name)

    def get_created_time(self, name):
        return self._current().get_created_time(name)

    def get_modified_time(self, name):
        return self._current().get_modified_time(name)

    def get_valid_name(self, name):
        return self._current().get_valid_name(name)

    def get_available_name(self, name, max_length=None):
        return self._current().get_available_name(name, max_length)

    def get_alternative_name(self, file_root, file_ext):
        return self._current().get_alternative_name(file_root, file_ext)

    def is_name_available(self, name, max_length=None):
        return self._current().is_name_available(name, max_length)

    def generate_filename(self, filename):
        return self._current().generate_filename(filename)
