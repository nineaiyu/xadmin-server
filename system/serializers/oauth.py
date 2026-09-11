# -*- coding: utf-8 -*-
"""第三方账号绑定序列化（只读：绑定关系只能由回调链路创建）。"""

from rest_framework import serializers

from system.models.oauth import UserOAuthBinding


class OAuthBindingSerializer(serializers.ModelSerializer):
    """绑定关系：user 只读且不可通过接口切换（防越权绑定到他人账号）。"""

    class Meta:
        model = UserOAuthBinding
        fields = ["pk", "user", "provider", "subject", "profile", "created_time"]
        read_only_fields = fields
