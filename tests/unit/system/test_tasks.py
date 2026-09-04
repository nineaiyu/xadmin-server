# -*- coding: utf-8 -*-
"""system 定时任务清理逻辑单元测试（system/utils/ctasks.py 与 system/tasks.py）。"""
import datetime
import uuid

import pytest
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from system import tasks
from system.models import OperationLog, UploadFile
from system.utils.ctasks import (
    auto_clean_black_token,
    auto_clean_operation_log,
    auto_clean_tmp_file,
)

pytestmark = pytest.mark.django_db


def _ago(days):
    """返回距今 days 天前的时间点（允许复数形式，用于构造未来时间）。"""
    return timezone.now() + datetime.timedelta(days=days)


def _make_operation_log(created_days_ago):
    obj = OperationLog.objects.create(module="test", method="GET", path="/api/demo")
    OperationLog.objects.filter(pk=obj.pk).update(created_time=_ago(-created_days_ago))
    return obj


def _make_upload(superuser, *, is_tmp, created_days_ago=None):
    obj = UploadFile.objects.create(
        filename="f.png",
        filesize=10,
        mime_type="image/png",
        md5sum="m" * 32,
        creator=superuser,
        is_tmp=is_tmp,
    )
    if created_days_ago is not None:
        UploadFile.objects.filter(pk=obj.pk).update(created_time=_ago(-created_days_ago))
    return obj


def _make_token(superuser, expires_days):
    return OutstandingToken.objects.create(
        user=superuser,
        jti=uuid.uuid4().hex,
        token="fake-token",
        expires_at=_ago(expires_days),
    )


class TestAutoCleanOperationLog:
    def test_removes_only_expired(self):
        expired = _make_operation_log(created_days_ago=400)
        fresh = _make_operation_log(created_days_ago=0)

        auto_clean_operation_log()

        assert not OperationLog.objects.filter(pk=expired.pk).exists()
        assert OperationLog.objects.filter(pk=fresh.pk).exists()

    def test_respects_custom_clean_day(self):
        old = _make_operation_log(created_days_ago=200)
        recent = _make_operation_log(created_days_ago=100)

        auto_clean_operation_log(clean_day=150)

        assert not OperationLog.objects.filter(pk=old.pk).exists()
        assert OperationLog.objects.filter(pk=recent.pk).exists()


class TestAutoCleanBlackToken:
    def test_removes_only_expired(self, superuser):
        expired = _make_token(superuser, expires_days=-2)
        active = _make_token(superuser, expires_days=2)

        auto_clean_black_token()

        assert not OutstandingToken.objects.filter(pk=expired.pk).exists()
        assert OutstandingToken.objects.filter(pk=active.pk).exists()


class TestAutoCleanTmpFile:
    def test_removes_only_tmp_and_expired(self, superuser):
        expired_tmp = _make_upload(superuser, is_tmp=True, created_days_ago=10)
        fresh_tmp = _make_upload(superuser, is_tmp=True, created_days_ago=0)
        expired_regular = _make_upload(superuser, is_tmp=False, created_days_ago=10)

        auto_clean_tmp_file()

        assert not UploadFile.objects.filter(pk=expired_tmp.pk).exists()
        assert UploadFile.objects.filter(pk=fresh_tmp.pk).exists()
        assert UploadFile.objects.filter(pk=expired_regular.pk).exists()


class TestPeriodicJobs:
    def test_auto_clean_operation_job(self):
        expired = _make_operation_log(created_days_ago=400)
        fresh = _make_operation_log(created_days_ago=0)

        tasks.auto_clean_operation_job()

        assert not OperationLog.objects.filter(pk=expired.pk).exists()
        assert OperationLog.objects.filter(pk=fresh.pk).exists()

    def test_auto_clean_black_token_job(self, superuser):
        expired = _make_token(superuser, expires_days=-10)
        active = _make_token(superuser, expires_days=10)

        tasks.auto_clean_black_token_job()

        assert not OutstandingToken.objects.filter(pk=expired.pk).exists()
        assert OutstandingToken.objects.filter(pk=active.pk).exists()

    def test_auto_clean_tmp_file_job(self, superuser):
        expired_tmp = _make_upload(superuser, is_tmp=True, created_days_ago=10)
        fresh_tmp = _make_upload(superuser, is_tmp=True, created_days_ago=0)

        tasks.auto_clean_tmp_file_job()

        assert not UploadFile.objects.filter(pk=expired_tmp.pk).exists()
        assert UploadFile.objects.filter(pk=fresh_tmp.pk).exists()