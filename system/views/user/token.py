#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""个人访问令牌（PAT）管理视图：个人凭证个人管。

- 取值域严格个人：任何用户（含超管）只见本人凭证，无管理员代管（登记边界）；
- 路由挂在 PERMISSION_WHITE_URL（个人安全操作，无需菜单权限，同 MFA 口径），
  但仍需登录（认证链生效）。
"""

from rest_framework.filters import OrderingFilter

from common.core.modelset import BaseModelSet
from system.models.token import PersonalAccessToken
from system.serializers.token import PersonalAccessTokenSerializer


class PersonalAccessTokenViewSet(BaseModelSet):
    """个人访问令牌"""

    queryset = PersonalAccessToken.objects.all()
    serializer_class = PersonalAccessTokenSerializer
    # PAT 是个人凭证：剥离默认数据权限过滤（默认拒绝会让本人凭证不可见），
    # 仅保留排序；取值域由 get_queryset 收口为本人
    filter_backends = (OrderingFilter,)
    ordering = ["-created_time"]
    ordering_fields = ["created_time", "last_used_time", "expired_at"]

    def get_queryset(self):
        # 严格个人取值域：含超管在内都只看本人凭证
        return self.queryset.filter(creator=self.request.user)
