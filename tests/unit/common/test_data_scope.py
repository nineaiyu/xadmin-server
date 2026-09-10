# -*- coding: utf-8 -*-
"""common/core/data_scope.py：规则编译器 + ScopeResult 代数 + 写入校验单测。"""

import pytest
from django.db.models import Q
from rest_framework.exceptions import ValidationError

from common.core.data_scope import (
    ALLOW_ALL,
    AND_MODE,
    DENY_ALL,
    OR_MODE,
    ScopeResult,
    build_rules_qs,
    combine,
    compile_condition,
    compile_grant,
    condition_result,
    ip_in_q,
    normalize_match_value,
    resolve_rule,
    rule_to_q,
    validate_rules,
)
from system.models import DataPermission

pytestmark = pytest.mark.django_db


def child_keys(q):
    return [c[0] for c in q.children if isinstance(c, tuple)]


class TestCombineAlgebra:
    """combine × {AND, OR} × {ALLOW_ALL, DENY_ALL, Q} 全组合。"""

    QA = condition_result(Q(a=1))
    QB = condition_result(Q(b=2))

    def test_and_with_deny_dominates(self):
        assert combine([self.QA, DENY_ALL], AND_MODE) is DENY_ALL
        assert combine([ALLOW_ALL, DENY_ALL], AND_MODE) is DENY_ALL

    def test_and_allow_is_unit(self):
        result = combine([self.QA, ALLOW_ALL, self.QB], AND_MODE)
        assert result.kind == ScopeResult.KIND_COND
        assert set(child_keys(result.q)) == {"a", "b"}

    def test_and_all_allow(self):
        assert combine([ALLOW_ALL, ALLOW_ALL], AND_MODE) is ALLOW_ALL

    def test_or_with_allow_dominates(self):
        assert combine([self.QA, ALLOW_ALL], OR_MODE) is ALLOW_ALL
        assert combine([ALLOW_ALL, DENY_ALL], OR_MODE) is ALLOW_ALL

    def test_or_deny_is_unit(self):
        result = combine([self.QA, DENY_ALL, self.QB], OR_MODE)
        assert result.kind == ScopeResult.KIND_COND
        assert len(result.q.children) == 2

    def test_or_all_deny(self):
        assert combine([DENY_ALL, DENY_ALL], OR_MODE) is DENY_ALL

    def test_empty_input_units(self):
        assert combine([], AND_MODE) is ALLOW_ALL
        assert combine([], OR_MODE) is DENY_ALL


class TestResolveRule:
    def test_all_type_forces_all_match(self):
        cond = resolve_rule({"field": "admin", "type": "value.all", "value": "*"}, None)
        assert cond["match"] == "all"

    def test_owner_resolves_user_pk(self, normal_user):
        cond = resolve_rule({"field": "admin", "type": "value.user.id", "value": "*"}, normal_user)
        assert cond["value"] == normal_user.pk

    def test_owner_department(self, normal_user, dept):
        normal_user.dept = dept
        cond = resolve_rule({"field": "admin__dept", "type": "value.user.dept.id", "value": "*"}, normal_user)
        assert cond["value"] == dept.pk
        assert cond["match"] == "exact"

    def test_owner_departments_includes_children(self, normal_user, dept):
        child = type(dept).objects.create(name="子部门", code="sub", parent=dept)
        normal_user.dept = dept
        cond = resolve_rule({"field": "dept_belong", "type": "value.user.dept.ids", "value": "*"}, normal_user)
        assert cond["match"] == "in"
        assert str(dept.pk) in [str(pk) for pk in cond["value"]]
        assert str(child.pk) in [str(pk) for pk in cond["value"]]

    def test_departments_json_value_flattened(self, dept):
        child = type(dept).objects.create(name="子部门2", code="sub2", parent=dept)
        import json

        cond = resolve_rule(
            {"field": "dept_belong", "type": "value.dept.ids", "value": json.dumps([str(dept.pk)])}, None
        )
        assert cond["match"] == "in"
        assert str(child.pk) in [str(pk) for pk in cond["value"]]

    def test_table_user_ids_normalized(self, normal_user):
        import json

        cond = resolve_rule(
            {
                "field": "admin",
                "type": "value.table.user.ids",
                "value": json.dumps([{"pk": str(normal_user.pk)}]),
                "match": "exact",  # 历史占位：运行时应强制归一为 in
            },
            normal_user,
        )
        assert cond["match"] == "in"
        assert cond["value"] == [str(normal_user.pk)]

    def test_rule_dict_not_mutated(self, normal_user):
        rule = {"field": "admin", "type": "value.user.id", "value": "*", "match": "exact"}
        resolve_rule(rule, normal_user)
        assert rule["value"] == "*"
        assert rule["type"] == "value.user.id"


class TestCompileCondition:
    def test_match_all_returns_allow(self, db):
        from demo.models import Book

        result = compile_condition(Book, {"field": "admin", "value": "*", "match": "all"})
        assert result is ALLOW_ALL

    def test_invalid_field_fail_closed(self, db):
        from demo.models import Book

        result = compile_condition(Book, {"field": "creat0r", "value": "x", "match": "exact"})
        assert result is DENY_ALL

    def test_dunder_field_ok(self, db):
        from demo.models import Book

        result = compile_condition(
            Book, {"field": "admin__dept", "value": "0f8fad5b-d9cb-469f-a165-70867728950e", "match": "exact"}
        )
        assert result.kind == ScopeResult.KIND_COND

    def test_dunder_field_value_type_mismatch_fail_closed(self, db):
        """跨表字段的 value 类型不匹配（UUID 字段配非 UUID）读取侧 fail-closed，不再查询期 500。"""
        from demo.models import Book

        assert compile_condition(Book, {"field": "admin__dept", "value": "abc", "match": "exact"}) is DENY_ALL

    def test_missing_field_or_value_skipped(self, db):
        from demo.models import Book

        assert compile_condition(Book, {"field": None, "value": "x", "match": "exact"}) is None
        assert compile_condition(Book, {"field": "name", "value": None, "match": "exact"}) is None

    def test_exclude_negates(self, db):
        from demo.models import Book

        result = compile_condition(Book, {"field": "isbn", "value": "x", "match": "exact", "exclude": True})
        assert result.q == ~Q(isbn__exact="x")

    def test_wildcard_value_is_allow_q(self, db):
        from demo.models import Book

        result = compile_condition(Book, {"field": "isbn", "value": "*", "match": "exact"})
        assert result.q == Q()

    def test_ip_in_wildcard_matches_all(self):
        assert ip_in_q("ip", ["*"]) == Q()

    def test_ip_in_prefix(self):
        assert child_keys(ip_in_q("ip", ["10.0"])) == ["ip__startswith"]

    def test_m2m_all_single_and_q(self):
        # m2m_all 单 Q 化：包含全部 = 逐值 AND，不再依赖外层组合模式
        q = rule_to_q({"field": "managers", "value": [1, 2], "match": "m2m_all"})
        assert q == (Q(managers__in=[1]) & Q(managers__in=[2]))

    def test_regex_invalid_falls_back(self):
        assert rule_to_q({"field": "name", "value": "([bad", "match": "regex"}) == Q(pk__isnull=True)


class TestLeaderRules:
    """value.leader.* 规则解析：部门子树展开与成员集合（主管语义）。"""

    def _rule(self, f_type):
        return {"table": "demo.book", "field": "dept_belong", "type": f_type, "value": "*", "match": "in"}

    def test_leader_departments_expands_subtree(self, dept, normal_user):
        child = type(dept).objects.create(name="子部门", code="leader-sub", parent=dept)
        dept.leader = normal_user
        dept.save(update_fields=["leader"])

        cond = resolve_rule(self._rule("value.leader.dept.ids"), normal_user)
        assert cond["match"] == "in"
        pks = {str(pk) for pk in cond["value"]}
        assert str(dept.pk) in pks
        assert str(child.pk) in pks

    def test_leader_departments_ignores_inactive_dept(self, dept, normal_user):
        """主管部门停用后不再授权（与 filter.py 只取 active 部门口径一致）。"""
        dept.leader = normal_user
        dept.is_active = False
        dept.save(update_fields=["leader", "is_active"])

        assert resolve_rule(self._rule("value.leader.dept.ids"), normal_user)["value"] == []

    def test_leader_users_returns_led_dept_members(self, dept, normal_user):
        dept.leader = normal_user
        dept.save(update_fields=["leader"])
        normal_user.dept = dept
        normal_user.save(update_fields=["dept"])

        cond = resolve_rule(self._rule("value.leader.user.ids"), normal_user)
        assert str(normal_user.pk) in {str(pk) for pk in cond["value"]}

    def test_non_leader_resolves_to_empty(self, dept, normal_user):
        """非主管：两类 leader 规则都解析为空集（恒假），不影响其他授权。"""
        assert resolve_rule(self._rule("value.leader.dept.ids"), normal_user)["value"] == []
        assert resolve_rule(self._rule("value.leader.user.ids"), normal_user)["value"] == []

    def test_leader_rules_are_placeholder_value(self, dept, normal_user):
        """value 的占位符（* / 空）不影响解析结果，由运行期注入真实 pk。"""
        dept.leader = normal_user
        dept.save(update_fields=["leader"])
        for placeholder in ("*", "", None):
            rule = {**self._rule("value.leader.dept.ids"), "value": placeholder}
            assert resolve_rule(rule, normal_user)["value"] != []


class TestCompileGrant:
    _seq = iter(range(10000))

    def make_dp(self, rules, mode=DataPermission.ModeChoices.OR):
        return DataPermission.objects.create(name=f"dp-test-{next(self._seq)}", rules=rules, mode_type=mode)

    def test_table_mismatch_returns_none(self, db):
        from demo.models import Book

        dp = self.make_dp([{"table": "other.model", "field": "x", "type": "value.text", "value": "1"}])
        assert compile_grant(dp, Book, None) is None

    def test_star_table_matches_any_model(self, db):
        from demo.models import Book

        dp = self.make_dp([{"table": "*", "field": "creator", "type": "value.text", "value": "*", "match": "exact"}])
        result = compile_grant(dp, Book, None)
        assert result is not None
        assert result.q == Q()

    def test_and_group_all_rules_all_returns_allow(self, db):
        """且模式 + 规则全部为 value.all：组放行（旧实现丢弃该组致全拒）。"""
        from demo.models import Book

        dp = self.make_dp(
            [
                {"table": "demo.book", "field": "admin", "type": "value.all", "value": "*"},
                {"table": "demo.book", "field": "isbn", "type": "value.all", "value": "*"},
            ],
            mode=DataPermission.ModeChoices.AND,
        )
        assert compile_grant(dp, Book, None) is ALLOW_ALL

    def test_and_group_all_ignored_keeps_others(self, db):
        from demo.models import Book

        dp = self.make_dp(
            [
                {"table": "demo.book", "field": "admin", "type": "value.all", "value": "*"},
                {"table": "demo.book", "field": "isbn", "type": "value.text", "value": "i1"},
            ],
            mode=DataPermission.ModeChoices.AND,
        )
        result = compile_grant(dp, Book, None)
        assert result.kind == ScopeResult.KIND_COND
        assert result.q == Q(isbn__exact="i1")


class TestValidateRules:
    def valid_rule(self):
        return {"table": "demo.book", "field": "admin", "type": "value.user.id", "value": "*", "match": "exact"}

    def test_valid_rule_passes(self):
        validate_rules([self.valid_rule()])

    def test_dunder_field_passes(self):
        validate_rules([{**self.valid_rule(), "field": "admin__dept", "type": "value.user.dept.id"}])

    def test_empty_rules_rejected(self):
        with pytest.raises(ValidationError):
            validate_rules([])

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            validate_rules([{**self.valid_rule(), "field": "creat0r"}])

    def test_unknown_table_rejected(self):
        with pytest.raises(ValidationError):
            validate_rules([{**self.valid_rule(), "table": "other.model"}])

    def test_unknown_type_rejected(self):
        with pytest.raises(ValidationError):
            validate_rules([{**self.valid_rule(), "type": "value.unknown"}])

    def test_exclude_all_rejected(self):
        rule = {**self.valid_rule(), "type": "value.all", "match": "all", "exclude": True}
        with pytest.raises(ValidationError):
            validate_rules([rule])

    def test_wildcard_field_requires_all_type(self):
        with pytest.raises(ValidationError):
            validate_rules([{**self.valid_rule(), "field": "*"}])

    def test_star_table_skips_model_field_check(self):
        validate_rules([{**self.valid_rule(), "table": "*", "field": "creator"}])

    def test_date_requires_number(self):
        with pytest.raises(ValidationError):
            validate_rules([{**self.valid_rule(), "type": "value.date", "value": "not-a-number"}])

    def test_datetime_range_requires_pair(self):
        with pytest.raises(ValidationError):
            validate_rules([{**self.valid_rule(), "type": "value.datetime.range", "value": ["2026-01-01 00:00:00"]}])

    def test_table_type_rejects_bogus_match(self):
        with pytest.raises(ValidationError):
            validate_rules([{**self.valid_rule(), "type": "value.table.user.ids", "match": "regex"}])

    def test_non_boolean_exclude_rejected(self):
        with pytest.raises(ValidationError):
            validate_rules([{**self.valid_rule(), "exclude": "yes"}])

    def test_non_dict_rule_rejected(self):
        with pytest.raises(ValidationError):
            validate_rules(["not-a-rule"])

    # ---------- 存量种子兼容与 match 口径（与前端下拉同源）----------

    def test_all_rule_legacy_match_values_pass(self):
        """ALL 规则历史 match 取值随意（"*"/""/缺失），校验须放行（读侧强制 all）。"""
        for match in ("*", "", None, "exact"):
            rule = {"table": "demo.book", "field": "admin", "type": "value.all", "value": "*", "match": match}
            validate_rules([rule])

    def test_all_rule_seed_shape_passes(self):
        """datapermission.json 全部数据授权原始形态（table=*, field=*, match=*）。"""
        validate_rules([{"table": "*", "field": "*", "type": "value.all", "match": "*", "value": "*"}])

    def test_field_class_lookups_accepted(self, db):
        """isnull / range / year 等字段 class lookups（前端 match 下拉同源）须放行。"""
        cases = [
            ("isnull", True),
            ("isnull", "true"),  # 前端普通输入框产物，写入侧归一放行
            ("range", ["2026-01-01 00:00:00", "2026-02-01 00:00:00"]),
            ("year", "2026"),
            ("iregex", "^x$"),
        ]
        for match, value in cases:
            validate_rules(
                [{**self.valid_rule(), "field": "created_time", "type": "value.text", "value": value, "match": match}]
            )

    def test_bogus_match_rejected(self, db):
        with pytest.raises(ValidationError):
            validate_rules([{**self.valid_rule(), "match": "bogus_lookup"}])

    def test_rule_to_q_all_match_defensive(self):
        """直接调用 rule_to_q 时 match=all 兜底恒真，不拼 Q(field__all=...)。"""
        assert rule_to_q({"field": "name", "value": "*", "match": "all"}) == Q()

    # ---------- 严格类型 lookup 的 value 收口（查询期 500 防线）----------

    def test_normalize_match_value(self):
        """归一表：isnull 接受 bool 与 "true"/"0"；range 只要 2 元；数字 lookup 转 int。"""
        assert normalize_match_value("isnull", "true") is True
        assert normalize_match_value("isnull", "False") is False
        assert normalize_match_value("isnull", "yes") is None
        assert normalize_match_value("range", ["a", "b"]) == ["a", "b"]
        assert normalize_match_value("range", ["a", "b", "c"]) is None
        assert normalize_match_value("year", "2026") == 2026
        assert normalize_match_value("year", "abc") is None
        assert normalize_match_value("exact", "x") == "x"

    def test_strict_lookup_value_normalized(self, db):
        """严格类型 value 写入归一放行，读侧拼出对应类型的 Q。"""
        for match, raw, expected in (
            ("isnull", "true", Q(created_time__isnull=True)),
            ("isnull", "0", Q(created_time__isnull=False)),
            ("year", "2026", Q(created_time__year=2026)),
        ):
            rule = {**self.valid_rule(), "type": "value.text", "field": "created_time", "value": raw, "match": match}
            validate_rules([rule])
            assert rule_to_q(rule) == expected

    def test_runtime_value_type_placeholder_passes(self, db):
        """value.user.id / value.user.dept.id 的 value 运行时注入，写入侧不得因占位值被误拒。

        守护：loadjson 存量 4 条授权（仅本人数据等）value 为 ""，加编译探测后曾整体报错。
        """
        for f_type, field in (("value.user.id", "admin"), ("value.user.dept.id", "admin__dept")):
            for placeholder in ("", "*"):
                validate_rules([{**self.valid_rule(), "type": f_type, "field": field, "value": placeholder}])

    def test_strict_lookup_bad_value_rejected(self, db):
        """isnull 非布尔 / range 非 2 元 / year 非数字：保存即拒（不再留到查询期 500）。"""
        for match, bad in (
            ("isnull", "yes"),
            ("range", ["2026-01-01 00:00:00"]),
            ("range", ["a", "b", "c"]),
            ("range", "1,2"),
            ("year", "abc"),
        ):
            with pytest.raises(ValidationError):
                validate_rules(
                    [
                        {
                            **self.valid_rule(),
                            "type": "value.text",
                            "field": "created_time",
                            "value": bad,
                            "match": match,
                        }
                    ]
                )

    def test_uncompilable_value_rejected(self, db):
        """归一管不到的非法 value（date 传非日期）由编译探测拦截，保存即拒。"""
        with pytest.raises(ValidationError):
            validate_rules(
                [{**self.valid_rule(), "type": "value.text", "field": "created_time", "value": "abc", "match": "date"}]
            )

    def test_legacy_strict_bad_value_fail_closed(self, db):
        """存量严格类型坏 value：读侧恒假（不参与组合），不再查询期抛错。"""
        from demo.models import Book

        for match, bad in (("isnull", "yes"), ("year", "abc")):
            result = compile_condition(Book, {"field": "created_time", "value": bad, "match": match})
            assert result is not None
            assert result.kind == ScopeResult.KIND_COND
            assert result.q == Q(pk__isnull=True)

    def test_legacy_uncompilable_value_fail_closed(self, db):
        """存量非法 value（date 传非日期）读侧整条规则 DENY_ALL，不让绑定用户 500。"""
        from demo.models import Book

        assert compile_condition(Book, {"field": "created_time", "value": "abc", "match": "date"}) is DENY_ALL

    def test_uncompilable_value_not_break_ip_network(self, db):
        """编译探测不得消费 ip_in 的 /24 生成器（探测后查询仍应命中网段）。"""
        from demo.models import Book

        q = ip_in_q("name", ["10.0.0.0/30"])
        assert compile_condition(Book, {"field": "name", "value": ["10.0.0.0/30"], "match": "ip_in"}) is not None
        assert len(q.children[0][1]) == 2  # 10.0.0.1 / 10.0.0.2

    def test_table_type_requires_non_empty_value(self, db):
        """value 归一为空集的 in 规则保存即拒（原先 isinstance 判断恒真，等于没校验）。"""
        rule = {**self.valid_rule(), "type": "value.table.user.ids", "match": "in", "value": []}
        with pytest.raises(ValidationError):
            validate_rules([rule])

    def test_build_rules_qs_all_by_type(self):
        """存量 ALL 规则 match 缺失/随意时也按 type 短路为恒真。"""
        assert build_rules_qs([{"type": "value.all", "field": "id", "value": ""}]) == [Q()]
        assert build_rules_qs([{"type": "value.all", "field": "*", "value": "*", "match": "*"}]) == [Q()]
