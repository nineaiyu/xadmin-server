# -*- coding: utf-8 -*-
"""验证码发送限流异常的 HTTP 语义。

限流是客户端错误，传输层必须归类 429（Too Many Requests），不得回退成
未预期异常 500——网关与监控按状态码统计；前端 ``errorStrategies`` 的
429 策略据此提示剩余秒数（``detail`` 由异常携带）。
"""

from common.core.response.exception import common_exception_handler
from common.utils.verify_code import CodeSendOverRate, CodeSendTooFrequently


class TestVerifyCodeRateLimitStatus:
    def test_over_rate_is_429(self):
        exc = CodeSendOverRate(42)
        assert exc.status_code == 429
        assert "42" in str(exc.detail)

    def test_too_frequently_is_429(self):
        exc = CodeSendTooFrequently(7)
        assert exc.status_code == 429
        assert "7" in str(exc.detail)

    def test_handler_maps_to_429_response(self):
        """全局异常处理器：HTTP 429 + 业务码随状态码写回（非未预期异常兜底 500）。"""
        response = common_exception_handler(CodeSendOverRate(30), {})
        assert response.status_code == 429
        assert response.data["code"] == 429
        assert "30" in str(response.data["detail"])
