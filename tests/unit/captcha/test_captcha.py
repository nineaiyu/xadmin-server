# -*- coding: utf-8 -*-
"""captcha 验证码单元测试（生成 / 大小写不敏感校验 / 一次性消费）。"""

import datetime

import pytest
from django.utils import timezone

from captcha.models import CaptchaStore
from captcha.utils import CaptchaAuth

pytestmark = pytest.mark.django_db


class TestCaptchaGenerate:
    def test_generate_returns_key_and_image(self):
        auth = CaptchaAuth()
        result = auth.generate()
        assert result["captcha_key"]
        assert result["captcha_image"].endswith(f"/{result['captcha_key']}/")
        assert CaptchaStore.objects.filter(hashkey=result["captcha_key"]).exists()


class TestCaptchaValid:
    def test_valid_case_insensitive_and_one_time(self):
        result = CaptchaAuth().generate()
        captcha = CaptchaStore.objects.get(hashkey=result["captcha_key"])

        # 大小写不敏感（random 验证码格式为小写字母，用大写验证）
        assert CaptchaAuth(captcha_key=result["captcha_key"]).valid(captcha.response.upper()) is True
        # 一次性消费：校验成功后记录被删除，再次校验失败
        assert CaptchaAuth(captcha_key=result["captcha_key"]).valid(captcha.response) is False

    def test_wrong_code_invalid(self):
        result = CaptchaAuth().generate()
        assert CaptchaAuth(captcha_key=result["captcha_key"]).valid("wrong") is False

    def test_expired_store_invalid(self):
        result = CaptchaAuth().generate()
        captcha = CaptchaStore.objects.get(hashkey=result["captcha_key"])
        captcha.expiration = timezone.now() - datetime.timedelta(minutes=1)
        captcha.save()
        assert CaptchaAuth(captcha_key=result["captcha_key"]).valid(captcha.response) is False


class TestCaptchaAtomicConsume:
    def test_valid_uses_single_delete_statement(self):
        """一次性语义靠单条 DELETE 的受影响行数判定。

        get() + delete() 是两条语句：并发同码在两条语句之间双双匹配成功，
        一次性语义被破坏（守卫断言 SQL 形态，改造回归会立刻暴露）。
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        result = CaptchaAuth().generate()
        captcha = CaptchaStore.objects.get(hashkey=result["captcha_key"])

        with CaptureQueriesContext(connection) as ctx:
            assert CaptchaAuth(captcha_key=result["captcha_key"]).valid(captcha.response) is True

        statements = [query["sql"].strip().upper() for query in ctx.captured_queries]
        assert len(statements) == 1, statements
        assert statements[0].startswith("DELETE")
