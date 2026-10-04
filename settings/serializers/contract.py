#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""BaseSettingViewSet 保存链路的序列化器侧显式契约。

19 个设置端点共用 BaseSettingViewSet.perform_update 这单一故障面，此前契约
隐晦：视图直接改写 ``serializer._data``、以 ``serializer._change_fields``
私有属性传递变更清单、约定序列化器"可选实现" ``post_save()``——组合行为
无文档、无测试，sms 的 post_save 读 ``self._data`` 做响应整形即靠这种耦合。

本混入把契约落到类型面：所有走设置保存链路的序列化器继承它，视图按
``set_response_data`` / ``change_fields`` / ``post_save`` 三个显式名字驱动。
时序与语义（视图 perform_update 为准）：

1. 仅 ``request.data`` 显式提交的键持久化（带 default 的可选字段未提交不落库，
   PUT 同口径）；write_only 密文字段提交空值 = 不修改（回退已存值）；
2. 视图回写响应载荷 ``set_response_data``——未提交键为运行时当前值、变更键
   为新值的合并视图；
3. 视图写入 ``change_fields``：实际落库且值发生变化的键名列表；
4. 调用 ``post_save()``：联动失效 / 响应整形在此钩子做——此时 change_fields
   与响应载荷（``response_data``）均已就绪、运行时 settings 经 pub/sub 异步
   热更中。

二开兼容：旧名 ``_change_fields`` / ``_data`` 保留读写别名并告
DeprecationWarning，一个版本周期后移除。
"""

import warnings

from django.utils.translation import gettext_lazy as _


class SettingSaveContractMixin:
    """与 BaseSettingViewSet.perform_update 配对的保存契约（详见模块 docstring）。"""

    change_fields: list[str] = []

    def set_response_data(self, data: dict) -> None:
        """回写保存后的响应载荷（未提交键 = 运行时当前值，变更键 = 新值）。"""
        self._data = data

    @property
    def response_data(self) -> dict:
        """当前响应载荷：post_save 阶段可读；就地改写即完成响应整形。"""
        return self._data

    @property
    def _change_fields(self):
        warnings.warn(
            str(_("settings 序列化器 _change_fields 已更名为 change_fields（显式契约），别名将在下个大版本移除")),
            DeprecationWarning,
            stacklevel=2,
        )
        return self.change_fields

    @_change_fields.setter
    def _change_fields(self, value):
        warnings.warn(
            str(_("settings 序列化器 _change_fields 已更名为 change_fields（显式契约），别名将在下个大版本移除")),
            DeprecationWarning,
            stacklevel=2,
        )
        self.change_fields = value

    def post_save(self) -> None:
        """保存后联动钩子：change_fields / response_data 已就绪。默认无操作。"""
