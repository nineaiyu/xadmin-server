# -*- coding: utf-8 -*-
"""可插拔文件存储后端守护测试。

覆盖：默认本地（零变化）/ 声明式切换 / 可选依赖缺失回退 / 委托重建 /
适配层（storage_local_path 远端缓存）/ 健康探测。
"""

import os
import sys
import types

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, Storage, default_storage

from common.core.config import SysConfig
from common.storage import (
    SwitchableStorage,
    build_delegate,
    clean_storage_cache,
    storage_backend_name,
    storage_cache_dir,
    storage_config,
    storage_exists,
    storage_is_local,
    storage_local_path,
    storage_open,
    storage_probe,
)
from common.storage import utils as storage_utils

pytestmark = pytest.mark.django_db


def _patch_config(monkeypatch, **values):
    """按 SysConfig property 口径注入存储配置（动态改配置必须 patch property）。"""
    for key, value in values.items():
        monkeypatch.setattr(type(SysConfig), key, property(lambda self, v=value: v), raising=False)


class _FakeRemoteStorage:
    """假远端后端：验证适配层在不依赖真实 S3 时的行为。"""

    is_local = False
    backend_name = "FakeRemote"

    def __init__(self, files=None):
        self.files = dict(files or {})

    def exists(self, name):
        return name in self.files

    def size(self, name):
        return len(self.files[name])

    def open(self, name, mode="rb"):
        import io

        return io.BytesIO(self.files[name])


class TestDefaultLocal:
    def test_default_storage_is_switchable_local(self):
        assert isinstance(default_storage, SwitchableStorage)
        assert storage_is_local() is True
        assert storage_backend_name() == "local"

    def test_local_roundtrip(self):
        name = default_storage.save("p4-test/hello.txt", ContentFile(b"storage-adapter"))
        try:
            assert storage_exists(name) is True
            with storage_open(name, "rb") as file:
                assert file.read() == b"storage-adapter"
            assert storage_local_path(name) and os.path.exists(storage_local_path(name))
            assert default_storage.size(name) == len(b"storage-adapter")
        finally:
            default_storage.delete(name)
        assert storage_exists(name) is False
        assert storage_local_path(name) is None

    def test_storage_probe_local(self):
        ok, cost = storage_probe()
        assert ok is True
        assert isinstance(cost, float)


class TestConfigDeclaration:
    def test_default_config_is_local(self):
        config = storage_config()
        assert config["backend"] == "local"
        assert config["bucket"] == ""

    def test_build_delegate_falls_back_without_dependency(self, monkeypatch):
        """s3 配置但未安装 django-storages / 未填 bucket：回退本地不崩溃。"""
        _patch_config(monkeypatch, FILE_STORAGE_BACKEND="s3", FILE_S3_BUCKET="")
        config = storage_config()
        assert config["backend"] == "s3"
        assert isinstance(build_delegate(config), FileSystemStorage)

    def test_build_delegate_uses_s3_when_available(self, monkeypatch):
        """安装依赖（以假模块注入）+ 配置齐全时使用 S3 后端并透传参数。"""
        captured = {}

        class _FakeS3Storage(FileSystemStorage):
            def __init__(self, **kwargs):
                captured.update(kwargs)
                super().__init__(location="/tmp/fake-s3", base_url="https://cdn.example.com/")

        fake_module = types.ModuleType("storages.backends.s3")
        fake_module.S3Storage = _FakeS3Storage
        monkeypatch.setitem(sys.modules, "storages.backends.s3", fake_module)
        _patch_config(
            monkeypatch,
            FILE_STORAGE_BACKEND="s3",
            FILE_S3_BUCKET="xadmin",
            FILE_S3_ENDPOINT="https://minio.example.com",
            FILE_S3_ADDRESSING_STYLE="path",
            FILE_S3_CUSTOM_DOMAIN="",
        )
        delegate = build_delegate(storage_config())
        assert isinstance(delegate, _FakeS3Storage)
        assert captured["bucket_name"] == "xadmin"
        assert captured["endpoint_url"] == "https://minio.example.com"
        assert captured["addressing_style"] == "path"
        # 未配自定义域名 → 生成签名 URL（私有桶）
        assert captured["querystring_auth"] is True

    def test_switchable_storage_rebuilds_on_config_change(self, monkeypatch):
        storage = SwitchableStorage()
        assert storage.is_local is True
        first = storage.delegate

        _patch_config(monkeypatch, FILE_STORAGE_BACKEND="s3", FILE_S3_BUCKET="")
        # bucket 未配 → 仍回退本地，但配置指纹已变（委托实例重建）
        second = storage.delegate
        assert isinstance(second, FileSystemStorage)
        assert storage.is_local is True
        assert first is not second or storage.backend_name == "local"


class TestAdapterForRemoteBackend:
    def test_local_path_downloads_to_cache_and_hits_cache(self, monkeypatch):
        remote = _FakeRemoteStorage({"a/b/doc.txt": b"remote-content"})
        monkeypatch.setattr(storage_utils, "default_storage", remote)
        target = storage_local_path("a/b/doc.txt")
        assert target and os.path.exists(target)
        assert target.startswith(storage_cache_dir())
        with open(target, "rb") as file:
            assert file.read() == b"remote-content"
        # 第二次命中缓存（mtime 刷新，不重复下载）
        assert storage_local_path("a/b/doc.txt") == target

    def test_local_path_returns_none_when_remote_missing(self, monkeypatch):
        monkeypatch.setattr(storage_utils, "default_storage", _FakeRemoteStorage({}))
        assert storage_local_path("missing.txt") is None

    def test_clean_storage_cache(self, monkeypatch):
        remote = _FakeRemoteStorage({"old.txt": b"x"})
        monkeypatch.setattr(storage_utils, "default_storage", remote)
        target = storage_local_path("old.txt")
        assert target
        # 保留期 0 天 → 立即清理
        assert clean_storage_cache(keep_days=0) == 0  # keep_days<=0 视为不清理
        os.utime(target, (0, 0))
        assert clean_storage_cache(keep_days=1) == 1
        assert not os.path.exists(target)


@pytest.mark.parametrize("key", ["FILE_S3_ACCESS_KEY", "FILE_S3_SECRET_KEY"])
def test_s3_credentials_registered_as_sensitive(key):
    """对象存储凭据必须登记为敏感键（落库加密 + 守护只减不增）。"""
    from common.core.credentials import SENSITIVE_SETTING_KEYS

    assert key in SENSITIVE_SETTING_KEYS


class _FakeS3Backend(Storage):
    """假 s3 后端（**不继承 FileSystemStorage**：is_local 判据必须为假）。"""

    def __init__(self, **kwargs):
        super().__init__()
        self.options = kwargs
        self.files = {}

    def _save(self, name, content):
        self.files[name] = content.read() if hasattr(content, "read") else bytes(content)
        return name

    def exists(self, name):
        return name in self.files

    def open(self, name, mode="rb"):
        import io

        return io.BytesIO(self.files[name])

    def size(self, name):
        return len(self.files[name])

    def delete(self, name):
        self.files.pop(name, None)

    def listdir(self, path):
        return [], []

    def url(self, name):
        return f"https://minio.example.com/{name}"


class _FailingReplica(_FakeS3Backend):
    def _save(self, name, content):
        raise OSError("replica down")


def _install_fake_s3_module(monkeypatch, created: list):
    class _S3Storage(_FakeS3Backend):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            created.append(self)

    fake_module = types.ModuleType("storages.backends.s3")
    fake_module.S3Storage = _S3Storage
    monkeypatch.setitem(sys.modules, "storages.backends.s3", fake_module)


class _FakeS3Client:
    def __init__(self, service, kwargs):
        self.service = service
        self.kwargs = kwargs
        self.calls = []

    def generate_presigned_url(self, operation, Params=None, ExpiresIn=None):
        self.calls.append((operation, Params, ExpiresIn))
        return f"https://minio.example.com/{Params['Bucket']}/{Params['Key']}?X-Amz-Signature=fake&Expires={ExpiresIn}"


def _install_fake_boto3(monkeypatch, holder: dict):
    fake_boto3 = types.ModuleType("boto3")

    def _client(service, **kwargs):
        client = _FakeS3Client(service, kwargs)
        holder["client"] = client
        return client

    fake_boto3.client = _client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    botocore_module = types.ModuleType("botocore")
    config_module = types.ModuleType("botocore.config")

    class _Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    config_module.Config = _Config
    botocore_module.config = config_module
    monkeypatch.setitem(sys.modules, "botocore", botocore_module)
    monkeypatch.setitem(sys.modules, "botocore.config", config_module)


class TestMirrorBackend:
    """搬迁窗口双写（mirror）：本地为主 + 对象存储尽力副本。"""

    def test_build_mirror_dual_write_and_local_read(self, monkeypatch):
        from common.storage import MirrorStorage

        created: list = []
        _install_fake_s3_module(monkeypatch, created)
        _patch_config(monkeypatch, FILE_STORAGE_BACKEND="mirror", FILE_S3_BUCKET="xadmin")
        delegate = build_delegate(storage_config())
        assert isinstance(delegate, MirrorStorage)
        assert created and created[0].options["file_overwrite"] is True

        name = delegate.save("p4-mirror/hello.txt", ContentFile(b"mirror-data"))
        try:
            assert delegate.exists(name) is True
            # 远端副本同对象名落盘
            assert created[0].files.get(name) == b"mirror-data"
            # 读 / 本地路径全走本地（业务零感知）
            with delegate.open(name, "rb") as file:
                assert file.read() == b"mirror-data"
            assert delegate.path(name)
        finally:
            delegate.delete(name)
        assert name not in created[0].files

    def test_switchable_is_local_and_name_for_mirror(self, monkeypatch):
        created: list = []
        _install_fake_s3_module(monkeypatch, created)
        _patch_config(monkeypatch, FILE_STORAGE_BACKEND="mirror", FILE_S3_BUCKET="xadmin")
        storage = SwitchableStorage()
        assert storage.is_local is True  # mirror 以本地为主存储
        assert storage.backend_name == "mirror"

    def test_mirror_replica_failure_does_not_block_upload(self, tmp_path):
        from common.storage import MirrorStorage

        local = FileSystemStorage(location=str(tmp_path))
        mirror = MirrorStorage(local, _FailingReplica())
        name = mirror.save("a.txt", ContentFile(b"ok"))
        assert local.exists(name)

    def test_mirror_falls_back_local_when_replica_unavailable(self, monkeypatch):
        _patch_config(monkeypatch, FILE_STORAGE_BACKEND="mirror", FILE_S3_BUCKET="")
        assert isinstance(build_delegate(storage_config()), FileSystemStorage)


class TestPresignedUrl:
    """预签名直连：仅 s3 后端可用，其余回退（None）。"""

    def test_none_for_local_backend(self):
        from common.storage import storage_presigned_url

        assert storage_presigned_url("a.txt") is None

    def test_none_for_mirror_backend(self, monkeypatch):
        from common.storage import storage_presigned_url

        created: list = []
        _install_fake_s3_module(monkeypatch, created)
        _patch_config(monkeypatch, FILE_STORAGE_BACKEND="mirror", FILE_S3_BUCKET="xadmin")
        assert storage_presigned_url("a.txt") is None

    def test_generates_for_s3_backend(self, monkeypatch):
        from common.storage import storage_presigned_url

        created: list = []
        holder: dict = {}
        _install_fake_s3_module(monkeypatch, created)
        _install_fake_boto3(monkeypatch, holder)
        _patch_config(
            monkeypatch,
            FILE_STORAGE_BACKEND="s3",
            FILE_S3_BUCKET="xadmin",
            FILE_S3_ENDPOINT="https://minio.example.com",
            FILE_S3_ADDRESSING_STYLE="path",
        )
        url = storage_presigned_url("docs/a.pdf", expires=120, download_filename="报告.pdf")
        assert url and "X-Amz-Signature" in url
        operation, params, expires = holder["client"].calls[0]
        assert operation == "get_object"
        assert params["Bucket"] == "xadmin"
        assert params["Key"] == "docs/a.pdf"
        assert params["ResponseContentDisposition"].startswith("attachment;")
        assert expires == 120
        assert holder["client"].kwargs["endpoint_url"] == "https://minio.example.com"

    def test_none_when_boto3_missing(self, monkeypatch):
        """s3 后端但可选依赖 boto3 未安装：返回 None（链路回退，不抛错）。"""
        from common.storage import storage_presigned_url

        created: list = []
        _install_fake_s3_module(monkeypatch, created)
        _patch_config(monkeypatch, FILE_STORAGE_BACKEND="s3", FILE_S3_BUCKET="xadmin")
        monkeypatch.setitem(sys.modules, "boto3", None)
        assert storage_presigned_url("a.txt") is None
