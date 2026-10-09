# -*- coding: utf-8 -*-
"""mypy 严格度提升试点（内核先行）配置守护。

试点口径写在 ``pyproject.toml`` 的 ``[[tool.mypy.overrides]]``：common 框架层启用
strict 子集，业务 app 维持增量收口。本用例守护三件事，防止试点静默回退：

1. 覆盖块存在且逐项 flag 显式启用（删掉任一项即红）；
2. 不得改用 ``strict = true``——mypy 2.x 的 per-module strict 会被**全局**应用
   （严格面泄漏到业务 app），逐项声明是把严格面限定在内核的唯一写法；
3. 业务 app 不受试点影响：全局段不得出现内核试点专用 flag（推广到 app 时须同步
   更新本用例的期望清单，属于有意的显式动作）。
"""

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 试点子集（与 pyproject 注释口径一致：strict 全量减去 disallow_subclassing_any
#: ——第三方基类无存根，启用会放大为纯存根产物的忽略）
PILOT_FLAGS = {
    "disallow_any_generics",
    "disallow_untyped_calls",
    "disallow_untyped_defs",
    "disallow_incomplete_defs",
    "disallow_untyped_decorators",
    "warn_return_any",
    "no_implicit_reexport",
    "strict_equality",
    "extra_checks",
}


@pytest.fixture(scope="module")
def mypy_config() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mypy"]


def test_kernel_override_declares_full_pilot_subset(mypy_config):
    overrides = [item for item in mypy_config.get("overrides", []) if "common" in item.get("module", [])]
    assert overrides, "pyproject.toml 缺少 common 内核的 mypy 严格子集覆盖块"
    kernel = overrides[0]
    enabled = {flag for flag in PILOT_FLAGS if kernel.get(flag) is True}
    assert enabled == PILOT_FLAGS, f"内核试点 flag 缺失：{sorted(PILOT_FLAGS - enabled)}"
    assert kernel.get("module") == ["common", "common.*"], "覆盖块须同时匹配内核包与其子模块"


def test_per_module_strict_not_used(mypy_config):
    # mypy 2.x：per-module 的 strict 走全局 set_strict_flags（不落 per-module 选项），
    # 写成 strict = true 会把严格面泄漏到全部业务 app —— 必须逐项声明
    for item in mypy_config.get("overrides", []):
        assert "strict" not in item, f"覆盖块 {item.get('module')} 不得使用 strict（mypy 2.x 会全局生效）"


def test_business_apps_not_under_pilot(mypy_config):
    for flag in PILOT_FLAGS:
        assert flag not in mypy_config, f"全局段出现试点专用 flag {flag}（业务 app 的推广须显式更新本用例期望）"
