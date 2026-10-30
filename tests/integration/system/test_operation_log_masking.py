# -*- coding: utf-8 -*-
"""脱敏规则对 JSON 字段与操作日志 changes 的生效守护。

两个历史缺口：
1. ``apply_mask`` 只处理字符串，规则挂在 JSON 列上时对 dict/list 是静默空操作；
2. 操作日志 ``changes`` 是整段 JSON 文本，字段级规则套在文本上无效（或把整段
   当成一个手机号），手机号/邮箱等历史值可被非授权角色从变更历史读到。

用例直接走序列化器（与列表/详情/导出同一条脱敏链路），不叠加数据权限对
「能否看到该行」的干扰。
"""

import json

import pytest
from django.test import RequestFactory

from audit.models import DataMaskRule, OperationLog
from audit.serializers.log import OperationLogSerializer
from audit.utils.mask import apply_mask
from identity.models import UserRole
from server.utils import set_current_request
from system.models import SystemConfig
from system.serializers.config import SystemConfigSerializer

pytestmark = pytest.mark.django_db

PHONE = "13812345678"
PHONE_NEW = "13987654321"
MASKED_PHONE = "138****5678"
MASKED_PHONE_NEW = "139****4321"


def _request(user, model_label, model, path="/api/audit/logs/operation"):
    request = RequestFactory().get(path)
    request.user = user
    # 字段权限未配置 = 零字段（fail-closed）：给足该模型的全字段白名单
    request.fields = {model_label: [field.name for field in model._meta.fields]}
    # BaseModelSerializer.__init__ 从 threadlocal 取请求（与运行时一致）
    set_current_request(request)
    return request


def _role(user, name):
    role = UserRole.objects.create(name=name, code=name.replace(" ", "-"))
    user.roles.add(role)
    return role


class TestJsonFieldMasking:
    """P1：规则挂在 JSON 列上必须对内部字符串生效。"""

    def test_apply_mask_recurses_into_dict_and_list(self):
        rule = {"mask_type": "phone", "keep_head": 3, "keep_tail": 4}

        assert apply_mask({"phone": PHONE}, rule) == {"phone": MASKED_PHONE}
        assert apply_mask([PHONE, {"nested": PHONE}], rule) == [
            MASKED_PHONE,
            {"nested": MASKED_PHONE},
        ]

    def test_json_column_rule_masks_serialized_value(self, normal_user, superuser):
        DataMaskRule.objects.create(
            model="system.systemconfig",
            field="value",
            mask_type="custom",
            pattern=r"1\d{10}",
            keep_head=3,
            keep_tail=4,
            is_active=True,
        )
        row = SystemConfig.objects.create(key="DEMO_JSON_PHONE", value={"phone": PHONE}, inherit=True, is_active=True)

        masked = SystemConfigSerializer(
            row, context={"request": _request(normal_user, "system.systemconfig", SystemConfig)}
        ).data
        original = SystemConfigSerializer(
            row, context={"request": _request(superuser, "system.systemconfig", SystemConfig)}
        ).data

        assert masked["value"] == {"phone": MASKED_PHONE}
        assert original["value"] == {"phone": PHONE}  # 超管豁免


class TestOperationLogChangesMasking:
    """P1：变更历史 old/new 按源模型规则与查看者角色脱敏。"""

    @staticmethod
    def _make_log(model_label="identity.userinfo", changes=None):
        return OperationLog.objects.create(
            module="test:change",
            object_pk="1",
            path="/api/identity/user/1",
            method="PATCH",
            status_code=1000,
            model_label=model_label,
            changes=json.dumps(
                changes
                if changes is not None
                else {"phone": {"old": PHONE, "new": PHONE_NEW}, "nickname": {"old": "a", "new": "b"}},
                ensure_ascii=False,
            ),
        )

    @staticmethod
    def _rule(roles=None):
        rule = DataMaskRule.objects.create(
            model="identity.userinfo",
            field="phone",
            mask_type="phone",
            keep_head=3,
            keep_tail=4,
            is_active=True,
        )
        if roles:
            rule.roles.set(roles)
        return rule

    @staticmethod
    def _changes(log, user):
        request = _request(user, "audit.operationlog", OperationLog)
        data = OperationLogSerializer(log, context={"request": request}).data
        return json.loads(data["changes"])

    def test_changes_masked_for_non_superuser(self, normal_user):
        self._rule()
        log = self._make_log()

        changes = self._changes(log, normal_user)

        assert changes["phone"] == {"old": MASKED_PHONE, "new": MASKED_PHONE_NEW}
        # 非敏感字段原样保留
        assert changes["nickname"] == {"old": "a", "new": "b"}

    def test_superuser_sees_original_changes(self, superuser):
        self._rule()
        log = self._make_log()

        changes = self._changes(log, superuser)

        assert changes["phone"] == {"old": PHONE, "new": PHONE_NEW}

    def test_history_row_without_model_label_untouched(self, normal_user):
        """升级前落库的历史行没有源模型标识：解析不出规则时原样返回，不阻断读取。"""
        self._rule()
        log = self._make_log(model_label="")

        changes = self._changes(log, normal_user)

        assert changes["phone"] == {"old": PHONE, "new": PHONE_NEW}

    def test_no_rule_means_original(self, normal_user):
        log = self._make_log()

        changes = self._changes(log, normal_user)

        assert changes["phone"] == {"old": PHONE, "new": PHONE_NEW}

    def test_role_restricted_rule_applies_only_to_matching_viewer(self, normal_user):
        viewer_role = _role(normal_user, "viewer")
        other_role = UserRole.objects.create(name="其他角色", code="other-role")
        self._rule(roles=[other_role])
        log = self._make_log()

        assert self._changes(log, normal_user)["phone"] == {"old": PHONE, "new": PHONE_NEW}

        self._rule(roles=[viewer_role])
        assert self._changes(log, normal_user)["phone"] == {
            "old": MASKED_PHONE,
            "new": MASKED_PHONE_NEW,
        }

    def test_unparseable_changes_returned_as_is(self, normal_user):
        """坏 JSON（历史截断行）不得让日志读取 500。"""
        self._rule()
        log = OperationLog.objects.create(
            module="test:change",
            path="/api/identity/user/1",
            status_code=1000,
            model_label="identity.userinfo",
            changes='{"phone": {"old": "138',
        )

        data = OperationLogSerializer(
            log, context={"request": _request(normal_user, "audit.operationlog", OperationLog)}
        ).data

        assert data["changes"] == '{"phone": {"old": "138'
