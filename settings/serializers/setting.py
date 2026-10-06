#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : settings
# author : ly_13
# date : 10/25/2024

from common.core.credentials import MASK, is_sensitive_setting_row
from common.core.serializers import BaseModelSerializer
from settings.models import Setting


class SettingSerializer(BaseModelSerializer):
    """Setting 行级序列化器（设置总表 list/detail/export/import 共用）。

    输出侧：敏感行（加密行与声明加密键，判定同源 ``is_sensitive_setting_row``）
    的 ``value`` 一律掩码——落库密文不外发，存量明文不借 list/export 落文件；
    非敏感行原样返回，设置总表仍是完整的配置台账。

    写入侧：掩码占位值永不落库。列表行数据被掩码后经「读-改-回写」或导入回放
    提交时，占位串会原样返回——更新丢弃该键（保留库内原值），新建视为未配置
    （模型默认空值）；提交真实新值不受影响。
    """

    class Meta:
        model = Setting
        fields = ["pk", "name", "value", "category", "is_active", "encrypted", "created_time"]
        read_only_fields = ["pk"]

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        if is_sensitive_setting_row(getattr(instance, "name", ""), getattr(instance, "encrypted", False)):
            ret["value"] = MASK
        return ret

    def validate(self, attrs):
        if attrs.get("value") == MASK:
            # 掩码占位 = 「已配置但值不下发」，与 write_only 密文留空不改的保存契约同义；
            # 丢弃该键：更新保留库内原值，新建落模型默认（未配置），占位串本身不落库
            attrs.pop("value", None)
        return attrs
