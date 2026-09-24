#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""消息模板变量：声明面与取值面一致性守护。

模板变量的"可写清单"（保存校验用 `template_variables()`）与"渲染取值"
（`get_template_vars()`）是两条独立路径，任一方向漂移都会让模板覆盖层静默失效：

- 只声明不取值 → 保存校验通过，渲染成空串（用户以为生效了）；
- 只取值不声明 → 模板写了该变量会被保存直接拒绝（用户以为不支持）。

因此对注册表里的每个消息类型逐项断言两个集合严格一致，并检查变量名形态
（必须匹配 ``{{ name }}`` 正则、不得占用通用变量名）。
"""

import pytest

from notifications.notifications import SYSTEM_MESSAGE_REGISTRY, USER_MESSAGE_REGISTRY
from notifications.template_registry import COMMON_VARIABLES, VARIABLE_PATTERN


def _samples():
    """产出 (cls, sample)：样例消息不可构造的类型（无 gen_test_msg 实现）跳过。"""
    for info in SYSTEM_MESSAGE_REGISTRY + USER_MESSAGE_REGISTRY:
        cls = info["cls"]
        try:
            sample = cls.gen_test_msg()
        except Exception:  # noqa: BLE001 无样例的类型不参与（预览同样不可用）
            sample = None
        if sample is not None:
            yield cls, sample


@pytest.mark.django_db
class TestTemplateVariablesConsistency:
    def test_declared_variables_all_have_values(self):
        """声明面 ⊆ 取值面：声明的变量必须有渲染值，否则模板写它只会渲染成空串。"""
        problems = []
        for cls, sample in _samples():
            missing = set(cls.template_variables()) - set(sample.get_template_vars())
            if missing:
                problems.append(f"{cls.__name__}: 已声明但无取值 {sorted(missing)}")
        assert not problems, "模板变量声明与取值不一致：\n" + "\n".join(problems)

    def test_values_all_declared(self):
        """取值面 ⊆ 声明面：渲染可用的变量必须可被保存校验接受，否则模板无法引用。"""
        problems = []
        for cls, sample in _samples():
            undeclared = set(sample.get_template_vars()) - set(cls.template_variables())
            if undeclared:
                problems.append(f"{cls.__name__}: 有取值但未声明 {sorted(undeclared)}")
        assert not problems, "模板变量取值未登记：\n" + "\n".join(problems)

    def test_sample_messages_render(self):
        """样例消息能渲染出标题（「模板预览」端点依赖它；构造异常应在此暴露而非线上吞掉）。"""
        problems = []
        for cls, sample in _samples():
            try:
                msg = sample.get_html_msg()
            except Exception as exc:  # noqa: BLE001 收集后统一断言
                problems.append(f"{cls.__name__}: 渲染抛错 {exc!r}")
                continue
            if not str(msg.get("subject") or "").strip():
                problems.append(f"{cls.__name__}: 样例标题为空")
        assert not problems, "\n".join(problems)

    def test_variable_names_are_valid_and_unreserved(self):
        """变量名必须能被 ``{{ name }}`` 解析，且不得占用通用变量名（同名不会被回填）。"""
        problems = []
        for cls, _sample in _samples():
            for key in cls.template_variables():
                probe = "{{ " + key + " }}"
                if not VARIABLE_PATTERN.fullmatch(probe):
                    problems.append(f"{cls.__name__}: 变量名非法 {key!r}")
                if key in COMMON_VARIABLES:
                    problems.append(f"{cls.__name__}: 占用通用变量名 {key!r}")
        assert not problems, "\n".join(problems)
