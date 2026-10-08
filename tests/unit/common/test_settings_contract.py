# -*- coding: utf-8 -*-
"""内核 settings 契约守护（common/settings_contract.py）。

守护面（纯静态 AST 扫描，不依赖运行期导入）：
1. 契约面 ↔ 源码读取双向一致：内核任何 ``settings.KEY`` / ``getattr(settings, "KEY", ...)``
   都必须在契约面登记；契约面登记的键也必须有真实读取点（防登记腐化）；
2. 缺省表达式锁步：``default_expr`` 必须与源码里的 getattr 缺省表达式一致；
   字面量缺省还要求与解析值一致（``default``）；
3. 消费方清单锁步：``consumers`` 必须等于实际读取该键的内核模块集合；
4. 混合读取形态显式登记：既直读又带缺省读取的键（缺失即报错 vs 回落默认值两种语义
   并存）白名单化，防止新增键无意引入混合语义；
5. 文档表锁步：``docs/architecture/kernel-package.md`` §三 的契约表与本模块同源渲染。

约定：本文件用 AST 扫描而不是正则，且跳过 ``settings_contract.py`` 自身（其
docstring 含示例读取形态）。
"""

import ast
import re
from pathlib import Path

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from common.settings_contract import (
    KERNEL_SETTINGS,
    REQUIRED,
    _Required,
    kernel_required_setting,
    kernel_setting,
    render_default,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
KERNEL_DIR = REPO_ROOT / "packages" / "xadmin-common" / "common"
CONTRACT_MODULE = KERNEL_DIR / "settings_contract.py"
DOC = REPO_ROOT / "docs" / "architecture" / "kernel-package.md"

# 混合读取形态白名单：既存在直读点（缺失即 AttributeError），又存在 getattr 缺省点。
# 语义：键缺失时「部分链路报错、部分链路回落默认」——仅这三处经评审保留：
#   - DEBUG / DEBUG_DEV：开发态判定允许回落 False，进程管理命令直读（部署必给）
#   - ALLOWED_HOSTS：Django 内置键（global_settings 恒有值），getattr 只是为了拿 None 语义
MIXED_READ_KEYS = {"DEBUG", "DEBUG_DEV", "ALLOWED_HOSTS"}


def _iter_kernel_modules():
    for path in sorted(KERNEL_DIR.rglob("*.py")):
        parts = set(path.parts)
        if "__pycache__" in parts or "migrations" in parts:
            continue
        if path == CONTRACT_MODULE:
            continue
        yield path


def scan_reads() -> dict[str, dict]:
    """扫描内核源码，返回 {键: {direct: bool, defaults: set[str], modules: set[str]}}。"""
    found: dict[str, dict] = {}

    def record(key: str, module: str, default_expr: str | None) -> None:
        entry = found.setdefault(key, {"direct": False, "defaults": set(), "modules": set()})
        entry["modules"].add(module)
        if default_expr is None:
            entry["direct"] = True
        else:
            entry["defaults"].add(default_expr)

    for path in _iter_kernel_modules():
        module = path.relative_to(KERNEL_DIR).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "settings"
                and node.attr.isupper()
            ):
                record(node.attr, module, None)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "settings"
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                default_expr = ast.unparse(node.args[2]) if len(node.args) > 2 else None
                record(node.args[1].value, module, default_expr)
    return found


def _resolve_symbol(name: str):
    """在契约消费模块内解析符号常量（如 DEFAULT_PRESET）的字面量取值。"""
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=\s*(.+?)\s*$", re.M)
    values = set()
    for path in _iter_kernel_modules():
        for matched in pattern.finditer(path.read_text(encoding="utf-8")):
            values.add(ast.literal_eval(matched.group(1)))
    assert len(values) == 1, f"符号 {name} 在契约消费模块内不唯一：{values}"
    return values.pop()


class TestContractSurfaceLockstep:
    def test_every_kernel_settings_read_is_registered(self):
        reads = scan_reads()
        unregistered = sorted(set(reads) - set(KERNEL_SETTINGS))
        assert unregistered == [], f"内核读取了未登记的 settings 键：{unregistered}（请登记到 settings_contract.py）"

    def test_every_registered_key_is_actually_read(self):
        reads = scan_reads()
        dead = sorted(set(KERNEL_SETTINGS) - set(reads))
        assert dead == [], f"契约面登记了没有读取点的键：{dead}（防登记腐化，请核对后移除）"

    def test_default_expr_matches_source(self):
        reads = scan_reads()
        mismatched = []
        for name, entry in KERNEL_SETTINGS.items():
            code_defaults = reads[name]["defaults"]
            if entry.default_expr is None:
                if code_defaults:
                    mismatched.append(f"{name}: 契约标记直读，源码含缺省 {sorted(code_defaults)}")
            elif code_defaults != {entry.default_expr}:
                mismatched.append(f"{name}: 契约 {entry.default_expr!r} vs 源码 {sorted(code_defaults)}")
        assert mismatched == [], "缺省表达式与源码不一致：\n  " + "\n  ".join(mismatched)

    def test_literal_defaults_match_resolved_value(self):
        mismatched = []
        for name, entry in KERNEL_SETTINGS.items():
            if entry.default_expr is None or entry.default is REQUIRED:
                continue
            try:
                resolved = ast.literal_eval(entry.default_expr)
            except (ValueError, SyntaxError):
                # 非常量表达式（符号引用，如 DEFAULT_PRESET）：在契约消费模块内静态解析字面量取值
                resolved = _resolve_symbol(entry.default_expr)
            if resolved != entry.default:
                mismatched.append(f"{name}: default={entry.default!r} vs {entry.default_expr}={resolved!r}")
        assert mismatched == [], "契约默认值与代码缺省表达式解析结果不一致：\n  " + "\n  ".join(mismatched)

    def test_consumers_match_scanned_modules(self):
        reads = scan_reads()
        mismatched = []
        for name, entry in KERNEL_SETTINGS.items():
            if set(entry.consumers) != reads[name]["modules"]:
                mismatched.append(f"{name}: 契约 {sorted(entry.consumers)} vs 源码 {sorted(reads[name]['modules'])}")
        assert mismatched == [], "消费方清单与源码不一致：\n  " + "\n  ".join(mismatched)

    def test_required_keys_have_no_default_read(self):
        reads = scan_reads()
        bad = sorted(
            name for name, entry in KERNEL_SETTINGS.items() if entry.default is REQUIRED and reads[name]["defaults"]
        )
        assert bad == [], f"契约标记为必给（REQUIRED）却存在缺省读取：{bad}"

    def test_mixed_read_keys_are_registered(self):
        reads = scan_reads()
        mixed = {name for name, data in reads.items() if data["direct"] and data["defaults"]}
        assert mixed == MIXED_READ_KEYS, (
            f"混合读取形态（直读 + 缺省读取）发生变化：新增 {sorted(mixed - MIXED_READ_KEYS)} / "
            f"消失 {sorted(MIXED_READ_KEYS - mixed)}——请评审后同步白名单与契约说明"
        )

    def test_framework_keys_are_django_builtins(self):
        """framework 标记只用于 Django 内置键（文档语义），逐键显式核对。"""
        expected = {
            "SECRET_KEY",
            "AUTH_USER_MODEL",
            "ALLOWED_HOSTS",
            "BASE_DIR",
            "MEDIA_ROOT",
            "MEDIA_URL",
            "EMAIL_HOST_USER",
            "EMAIL_SUBJECT_PREFIX",
            "DEBUG",
            "API_MODEL_MAP",
            "LANGUAGE_CODE",
            "DEFAULT_CHARSET",
        }
        actual = {name for name, entry in KERNEL_SETTINGS.items() if entry.framework}
        assert actual == expected, f"framework 标记集合变化：{sorted(actual ^ expected)}"


class TestReadHelpers:
    def test_required_sentinel_semantics(self):
        from common.settings_contract import REQUIRED as SENTINEL

        assert repr(SENTINEL) == "REQUIRED"
        assert SENTINEL is KERNEL_SETTINGS["SECRET_KEY"].default
        assert SENTINEL is _Required()  # 单例：重复构造不产生新哨兵
        import pytest

        with pytest.raises(TypeError, match="不支持真值判断"):
            bool(SENTINEL)

    def test_kernel_setting_required_key_delegates_to_required_reader(self):
        with override_settings(SECRET_KEY="delegate-key"):
            assert kernel_setting("SECRET_KEY") == "delegate-key"

    def test_kernel_setting_falls_back_to_contract_default(self):
        with override_settings():
            del django_settings.MODULE_PRESET
            assert kernel_setting("MODULE_PRESET") == "full"

    def test_kernel_setting_returns_configured_value(self):
        with override_settings(MODULE_PRESET="core"):
            assert kernel_setting("MODULE_PRESET") == "core"

    def test_kernel_setting_rejects_unregistered_key(self):
        import pytest

        with pytest.raises(KeyError, match="未登记"):
            kernel_setting("NOT_A_KERNEL_SETTING")

    def test_kernel_required_setting_rejects_unregistered_key(self):
        import pytest

        with pytest.raises(KeyError, match="未登记"):
            kernel_required_setting("NOT_A_KERNEL_SETTING")

    def test_required_setting_missing_raises_improperly_configured(self):
        import pytest

        with override_settings():
            del django_settings.SECRET_KEY
            with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
                kernel_required_setting("SECRET_KEY")

    def test_required_setting_returns_value_when_present(self):
        with override_settings(SECRET_KEY="contract-test-key"):
            assert kernel_required_setting("SECRET_KEY") == "contract-test-key"


class TestDocTableLockstep:
    """docs/architecture/kernel-package.md §三 契约表须与本模块同源（键 / 类型 / 缺省）。"""

    @staticmethod
    def _rows() -> dict[str, tuple[str, str]]:
        text = DOC.read_text(encoding="utf-8")
        section = text.split("## 三、内核 settings 契约", 1)
        assert len(section) == 2, "内核 settings 契约章节缺失（## 三、内核 settings 契约）"
        rows: dict[str, tuple[str, str]] = {}
        for line in section[1].splitlines():
            # Markdown 表格里的 `\|` 是类型列中联合类型的转义写法：按「未转义竖线」切列后再还原
            cells = [cell.replace("\\|", "|").strip() for cell in re.split(r"(?<!\\)\|", line)]
            if len(cells) < 4 or not re.fullmatch(r"`[A-Z][A-Z0-9_]*`", cells[1]):
                continue
            rows[cells[1].strip("`")] = (cells[2], cells[3])
        return rows

    def test_doc_rows_match_contract(self):
        rows = self._rows()
        assert rows, "文档契约表为空（表头形如 | `KEY` | 类型 | 缺省 |）"
        missing = sorted(set(KERNEL_SETTINGS) - set(rows))
        extra = sorted(set(rows) - set(KERNEL_SETTINGS))
        assert missing == [] and extra == [], f"文档表与契约面不一致：缺 {missing}，多 {extra}"

        mismatched = []
        for name, entry in KERNEL_SETTINGS.items():
            kind, default = rows[name]
            if kind != entry.kind:
                mismatched.append(f"{name}: 类型 文档 {kind!r} vs 契约 {entry.kind!r}")
            if default != render_default(entry):
                mismatched.append(f"{name}: 缺省 文档 {default!r} vs 契约 {render_default(entry)!r}")
        assert mismatched == [], "文档表与契约面字段不一致：\n  " + "\n  ".join(mismatched)
