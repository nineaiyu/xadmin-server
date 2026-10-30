# -*- coding: utf-8 -*-
"""用户搜索序列化器不得输出密码/凭据字段。"""

from identity.views.search.user import SearchUserSerializer


def test_search_user_serializer_excludes_password():
    serializer = SearchUserSerializer()
    assert "password" not in serializer.fields


def test_search_user_serializer_output_has_no_sensitive_values():
    serializer = SearchUserSerializer()
    sensitive = {"password", "otp_secret_key", "token_hash", "client_secret_hash"}
    assert sensitive.isdisjoint(serializer.fields)
