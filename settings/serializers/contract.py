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

响应扩展：本混入同时承担「视图侧只读派生字段」的输出注入——序列化时调用
视图的 ``get_readonly_values(data)``（如设置 tab 的「实际生效状态」诊断）,
把计算值合并进响应载荷。不声明为 serializer 字段,列元数据与保存契约均不受
影响;视图未实现该钩子时为零开销跳过。
"""

import warnings
from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework.serializers import Serializer


class SettingReadonlyValuesMixin(Serializer):
    """序列化输出注入视图侧只读派生字段（不进列元数据、不参与保存校验）。

    继承 Serializer 而非裸 mixin：to_representation / context 由宿主提供，
    类型检查可校验；所有使用点均与 serializers.Serializer 组合，MRO 线性不受影响。
    """

    def to_representation(self, instance: Any) -> Any:
        data = super().to_representation(instance)
        getter = getattr(self.context.get("view"), "get_readonly_values", None)
        if callable(getter):
            extra = getter(data)
            if extra:
                data.update(extra)
        return data


class SettingSaveContractMixin(SettingReadonlyValuesMixin):
    """与 BaseSettingViewSet.perform_update 配对的保存契约（详见模块 docstring）。"""

    change_fields: list[str] = []

    def set_response_data(self, data: dict[str, Any]) -> None:
        """回写保存后的响应载荷（未提交键 = 运行时当前值，变更键 = 新值）。"""
        self._data = data

    @property
    def response_data(self) -> dict[str, Any]:
        """当前响应载荷：post_save 阶段可读；就地改写即完成响应整形。"""
        return self._data

    @property
    def _change_fields(self) -> list[str]:
        warnings.warn(
            str(
                _(
                    "Settings serializer _change_fields has been renamed to change_fields "
                    "(explicit contract); the alias will be removed in the next major version"
                )
            ),
            DeprecationWarning,
            stacklevel=2,
        )
        return self.change_fields

    @_change_fields.setter
    def _change_fields(self, value: list[str]) -> None:
        warnings.warn(
            str(
                _(
                    "Settings serializer _change_fields has been renamed to change_fields "
                    "(explicit contract); the alias will be removed in the next major version"
                )
            ),
            DeprecationWarning,
            stacklevel=2,
        )
        self.change_fields = value

    def post_save(self) -> None:
        """保存后联动钩子：change_fields / response_data 已就绪。默认无操作。"""
