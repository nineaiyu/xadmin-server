# -*- coding: utf-8 -*-
"""mypy 严格度提升试点（内核先行 + 逐 app 推广）配置守护。

试点口径写在 ``pyproject.toml`` 的 ``[[tool.mypy.overrides]]``：common 框架层与
**已推广的业务 app** 启用同一 strict 子集，未推广的 app 维持增量收口。本用例守护四件事，
防止试点静默回退：

1. 内核覆盖块存在且逐项 flag 显式启用（删掉任一项即红）；
2. 不得改用 ``strict = true``——mypy 2.x 的 per-module strict 会被**全局**应用
   （严格面泄漏到业务 app），逐项声明是把严格面限定在受控范围里的唯一写法；
3. 业务 app 覆盖块与内核同口径（flag 集合一致），且入列 app 与 ``PROMOTED_APPS``
   显式清单双向一致——推广一个 app 必须同时改配置与本清单（有意的显式动作，
   顺带让「已推广面」在测试里可读）；
4. 全局段不得出现试点专用 flag（严格面只经覆盖块生效）。
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

#: 已推广到 strict 子集的业务 app（按体量从小到大推进，只增不减；
#: 新增一项 = 该 app 存量告警已清零并入列，配置块同步追加 "app" 与 "app.*"）
PROMOTED_APPS = [
    "captcha",
    "demo",
    "devtools",
    "integrations",
    "mfa",
    "server",
    "settings",
    "audit",
    "file",
    "demo_seed",
    "notifications",
    "task",
    "message",
    "dataset",
    "approval",
    "ai",
    "system",
    "identity",
]


@pytest.fixture(scope="module")
def mypy_config() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mypy"]


@pytest.fixture(scope="module")
def overrides(mypy_config) -> list:
    return mypy_config.get("overrides", [])


def test_kernel_override_declares_full_pilot_subset(overrides):
    kernel = next((item for item in overrides if "common" in item.get("module", [])), None)
    assert kernel, "pyproject.toml 缺少 common 内核的 mypy 严格子集覆盖块"
    enabled = {flag for flag in PILOT_FLAGS if kernel.get(flag) is True}
    assert enabled == PILOT_FLAGS, f"内核试点 flag 缺失：{sorted(PILOT_FLAGS - enabled)}"
    assert kernel.get("module") == ["common", "common.*"], "覆盖块须同时匹配内核包与其子模块"


def test_business_override_matches_kernel_subset(overrides):
    business = next((item for item in overrides if "captcha" in item.get("module", [])), None)
    assert business, "pyproject.toml 缺少业务 app 的 mypy 严格子集覆盖块"
    enabled = {flag for flag in PILOT_FLAGS if business.get(flag) is True}
    assert enabled == PILOT_FLAGS, f"业务 app 试点 flag 与内核口径不一致：{sorted(PILOT_FLAGS - enabled)}"
    # 覆盖块只写 flag：不得夹带其他 mypy 选项（保持与内核块同形）
    extra = {key for key in business if key not in PILOT_FLAGS and key != "module"}
    assert not extra, f"业务 app 覆盖块出现非子集选项：{sorted(extra)}"


def test_promoted_apps_list_matches_config(overrides):
    business = next((item for item in overrides if "captcha" in item.get("module", [])), None)
    assert business, "pyproject.toml 缺少业务 app 的 mypy 严格子集覆盖块"
    expected = [name for app in PROMOTED_APPS for name in (app, f"{app}.*")]
    assert business.get("module") == expected, (
        "已推广 app 清单与 pyproject 覆盖块不一致（推广一个 app 须同步本清单与配置块）"
    )


def test_per_module_strict_not_used(overrides):
    # mypy 2.x：per-module 的 strict 走全局 set_strict_flags（不落 per-module 选项），
    # 写成 strict = true 会把严格面泄漏到全部业务 app —— 必须逐项声明
    for item in overrides:
        assert "strict" not in item, f"覆盖块 {item.get('module')} 不得使用 strict（mypy 2.x 会全局生效）"


def test_global_section_has_no_pilot_flags(mypy_config):
    for flag in PILOT_FLAGS:
        assert flag not in mypy_config, f"全局段出现试点专用 flag {flag}（严格面只经覆盖块生效）"
