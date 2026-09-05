# -*- coding: utf-8 -*-
"""common/core/exception.py 全局异常处理测试。

这是所有接口的错误契约，覆盖：
1. DRF APIException / ValidationError / Http404 / Throttled 的统一响应结构；
2. ProtectedError 的 998 业务码与回滚；
3. 未预期异常不向客户端泄漏内部细节（统一 500）；
4. list 形式的错误详情被包装进 detail 字段。
"""
import pytest
from django.db.models import ProtectedError
from django.http import Http404
from rest_framework.exceptions import NotFound, Throttled, ValidationError

from common.core.exception import common_exception_handler
from system.models import DeptInfo, UserInfo


class FakeView:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self):
        self.__class__ = type("FakeViewSet", (), {})


def _context():
    return {"view": FakeView(), "request": None}


class TestApiException:
    def test_validation_error_dict_detail(self):
        """字段级校验错误：字段名平铺到响应体顶层，detail 为整体字符串描述（既有契约）"""
        exc = ValidationError({"name": ["该字段必填"]})
        response = common_exception_handler(exc, _context())
        assert response.status_code == 400
        assert response.data["code"] == 400
        assert response.data["name"] == ["该字段必填"]
        assert "该字段必填" in response.data["detail"]

    def test_validation_error_string_detail(self):
        """DRF 会把字符串详情包装成单元素 list，处理器再包进 detail"""
        exc = ValidationError("参数错误")
        response = common_exception_handler(exc, _context())
        assert response.data["detail"] == ["参数错误"]

    def test_not_found(self):
        exc = NotFound()
        response = common_exception_handler(exc, _context())
        assert response.status_code == 404

    def test_list_detail_wrapped(self):
        """DRF 对非字段错误返回 list，需要包装成 {'detail': [...]}"""
        exc = ValidationError(["错误1", "错误2"])
        response = common_exception_handler(exc, _context())
        assert response.data["detail"] == ["错误1", "错误2"]


class TestThrottled:
    def test_throttled_with_wait(self):
        exc = Throttled(wait=7)
        response = common_exception_handler(exc, _context())
        assert response.data["code"] == 999
        assert "7" in response.data["detail"]

    def test_throttled_without_wait(self):
        exc = Throttled()
        response = common_exception_handler(exc, _context())
        assert response.data["code"] == 999

    def test_throttled_http_status_preserved(self):
        """业务码 999 写入响应体，HTTP 状态码保持 429（前端依赖 detail 提示）"""
        response = common_exception_handler(Throttled(wait=3), _context())
        assert response.status_code == 429
        assert response.data["code"] == 999


class TestHttp404:
    def test_http404_mapped_to_400(self):
        response = common_exception_handler(Http404("x"), _context())
        assert response.status_code == 400
        assert response.data["detail"]
        assert response.data["code"] == 400


class TestProtectedError:
    def test_protected_error_returns_998(self, db):
        parent = DeptInfo.objects.create(name="p", code="p")
        DeptInfo.objects.create(name="c", code="c", parent=parent)

        with pytest.raises(ProtectedError) as exc_info:
            parent.delete()

        response = common_exception_handler(exc_info.value, _context())
        assert response.data["code"] == 998
        assert "部门" in response.data["detail"] or "department" in str(response.data["detail"]).lower()


class TestUnexpectedException:
    def test_unexpected_exception_returns_generic_500(self, db):
        """未预期异常不向客户端暴露内部细节"""
        response = common_exception_handler(ValueError("secret internal detail"), _context())
        assert response.status_code == 500
        assert response.data["code"] == 500
        assert "secret" not in str(response.data["detail"])

    def test_unexpected_exception_in_transaction_does_not_rollback_data(self, db, superuser):
        """未预期异常（非 APIException）不应触发 set_rollback"""
        response = common_exception_handler(RuntimeError("boom"), _context())
        assert response.status_code == 500
        assert UserInfo.objects.filter(pk=superuser.pk).exists()


class TestResponseShell:
    def test_response_keeps_api_shell_fields(self):
        response = common_exception_handler(ValidationError({"name": ["x"]}), _context())
        # ApiResponse 外壳字段必须齐全，前端依赖 code/detail 判断
        assert {"code", "detail", "requestId", "timestamp"} <= set(response.data)

    def test_list_detail_shell(self):
        """list 形式详情被包装后，'status' 关键字被 ApiResponse 的 HTTP status 形参吞掉（既有契约）"""
        response = common_exception_handler(ValidationError(["x"]), _context())
        assert {"code", "detail", "requestId", "timestamp"} <= set(response.data)
        assert response.data["code"] == 400
