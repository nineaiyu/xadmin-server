#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通讯录序列化器：只读轻量名录（部门 + 岗位 + 联系方式）。

岗位为人员维度标签（pk/name/code），不参与权限判定；字段集刻意精简，
不暴露角色/数据权限等管理面信息（通讯录面向普通使用者）。
"""

from identity.serializers.user import UserSerializer


class DirectorySerializer(UserSerializer):
    class Meta(UserSerializer.Meta):
        fields = [
            "pk",
            "avatar",
            "username",
            "nickname",
            "gender",
            "dept",
            "posts",
            "email",
            "phone",
            "is_active",
            "last_login",
            "date_joined",
        ]
        table_fields = [
            "pk",
            "avatar",
            "username",
            "nickname",
            "gender",
            "dept",
            "posts",
            "email",
            "phone",
            "last_login",
        ]
