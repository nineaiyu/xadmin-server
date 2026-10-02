# -*- coding: utf-8 -*-
"""运行日志脱敏过滤器（server.logging.SensitiveDataFilter，O8-6）守护测试。

覆盖：三种键值形态（JSON 双引号 / repr 单引号 / kv 裸值）、裸数字不掩码
（业务响应包络 code: 200 是日志主诊断信息）、异常栈与 args 记录、幂等
（同一 record 流经多个 handler 重复掩码不变形）、过滤器自身故障放行。
名单与操作日志链路同源（common.core.sensitive.SENSITIVE_FIELDS）。
"""

import logging

from common.core.sensitive import SENSITIVE_FIELDS
from server.logging import SensitiveDataFilter


def _record(msg, exc_info=None):
    return logging.LogRecord("mask_test", logging.INFO, "path", 1, msg, None, exc_info)


class TestSensitiveDataFilter:
    def test_json_double_quoted_masked(self):
        f = SensitiveDataFilter()
        record = _record('login failed with {"password": "hunter2", "user": "alice"}')
        assert f.filter(record) is True
        message = record.getMessage()
        assert "hunter2" not in message
        assert '"password": "*******"' in message
        assert '"user": "alice"' in message

    def test_repr_single_quoted_masked(self):
        f = SensitiveDataFilter()
        record = _record("mfa confirm {'code': '874201', 'method': 'password'}")
        assert f.filter(record) is True
        message = record.getMessage()
        assert "874201" not in message
        assert "'code': '******'" in message
        assert "'method': 'password'" in message

    def test_kv_bare_masked(self):
        f = SensitiveDataFilter()
        record = _record("auth failed password=hunter2 user=alice")
        assert f.filter(record) is True
        message = record.getMessage()
        assert "hunter2" not in message
        assert "password=*******" in message
        assert "user=alice" in message

    def test_bare_numeric_kept_for_business_code(self):
        """裸数字不掩码：响应包络 code: 200 是排障主信息，数字形态非口令常态。"""
        f = SensitiveDataFilter()
        record = _record('{"code": 200, "data": null}')
        assert f.filter(record) is True
        assert record.getMessage() == '{"code": 200, "data": null}'

    def test_quoted_numeric_code_masked(self):
        """带引号的数字串按口令/验证码形态掩码。"""
        f = SensitiveDataFilter()
        record = _record('{"code": "1000"}')
        assert f.filter(record) is True
        assert record.getMessage() == '{"code": "****"}'

    def test_jwt_response_fields_masked(self):
        f = SensitiveDataFilter()
        record = _record('response {"access": "eyJhbGciOi.J9.signature", "refresh": "e.R.s"}')
        assert f.filter(record) is True
        message = record.getMessage()
        assert "eyJhbGciOi" not in message
        assert message.count("*") == len("eyJhbGciOi.J9.signature") + len("e.R.s")

    def test_exception_stack_masked(self):
        f = SensitiveDataFilter()
        try:
            raise ValueError('stack has "token": "leak-value" inside')
        except ValueError as exc:
            record = _record("boom", exc_info=(type(exc), exc, exc.__traceback__))
        assert f.filter(record) is True
        assert record.exc_text is not None
        assert "leak-value" not in record.exc_text

    def test_args_record_masked(self):
        f = SensitiveDataFilter()
        record = logging.LogRecord("mask_test", logging.INFO, "path", 1, "user=%s token=%s", ("alice", "tok-xyz"), None)
        assert f.filter(record) is True
        assert record.getMessage() == "user=alice token=*******"

    def test_idempotent_across_handlers(self):
        """同一 record 流经 console + 文件两个 handler：二次过滤不二次变形。"""
        f = SensitiveDataFilter()
        record = _record('{"password": "hunter2"}')
        assert f.filter(record) is True
        first = record.getMessage()
        assert f.filter(record) is True
        assert record.getMessage() == first == '{"password": "*******"}'

    def test_filter_failure_fails_open(self, monkeypatch):
        """脱敏自身故障放行原样：过滤器不吞日志。"""
        f = SensitiveDataFilter()

        def _boom(_text):
            raise RuntimeError("mask broken")

        monkeypatch.setattr(type(f), "_mask_text", classmethod(lambda cls, text: _boom(text)))
        record = _record('{"password": "hunter2"}')
        assert f.filter(record) is True
        assert "hunter2" in record.getMessage()

    def test_registry_shared_with_oplog(self):
        """名单与操作日志链路同源：oplog_recorder 再导出的就是这一份。"""
        from common.core.oplog_recorder import SENSITIVE_FIELDS as OPLOG_FIELDS

        assert SENSITIVE_FIELDS is OPLOG_FIELDS
        assert "password" in SENSITIVE_FIELDS and "verify_token" in SENSITIVE_FIELDS
