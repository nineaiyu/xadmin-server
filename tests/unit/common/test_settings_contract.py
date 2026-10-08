# -*- coding: utf-8 -*-
"""内核 settings 契约守护（common/settings_contract.py）。

守护面（AST 静态扫描，不依赖运行期导入）：

1. **读取形态唯一**：内核任何地方都不得直接读 ``settings.KEY`` / ``getattr(settings, ...)``
   （含 ``dj_settings`` 等别名），一律走契约访问器 ``kernel_setting`` / ``kernel_required_setting``；
   内核也不得再 import ``django.conf.settings``（唯一例外是契约模块自身的读取实现）；
2. **契约面 ↔ 源码双向一致**：访问器读取的每个键都必须在契约面登记；契约面登记的键也必须有
   真实读取点（防登记腐化）；
3. **访问器形态与缺省状态锁步**：``REQUIRED`` 键必须用 ``kernel_required_setting``（缺失 fail-fast），
   有缺省的键必须用 ``kernel_setting``（缺失回落契约缺省值）；
4. **消费方清单锁步**：``consumers`` 必须等于实际读取该键的内核模块集合；
5. **零配置回落**：每个有缺省的键在键缺失时都按契约缺省值工作，且回落值是可变的容器时返回副本
   （不污染契约面共享对象）；每个 ``REQUIRED`` 键缺失时报 ``ImproperlyConfigured`` 并带用途提示；
6. **framework 标记自证**：``framework=True`` ↔ ``django.conf.global_settings`` 真正内置（双向）；
7. **文档表锁步**：``docs/architecture/kernel-package.md`` §三 的契约表与「宿主最小对接面」
   与本模块同源渲染。

约定：本文件用 AST 扫描而不是正则，且跳过 ``settings_contract.py`` 自身（其 docstring 含
读取形态示例，读取实现也在此）。
"""

import ast
import copy
import re
from pathlib import Path

from django.conf import global_settings
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
    required_kernel_settings,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
KERNEL_DIR = REPO_ROOT / "packages" / "xadmin-common" / "common"
CONTRACT_MODULE = KERNEL_DIR / "settings_contract.py"
DOC = REPO_ROOT / "docs" / "architecture" / "kernel-package.md"

#: 内核源码里禁止出现的 settings 读取对象名（含历史别名，防换名绕过）
FORBIDDEN_SETTINGS_NAMES = {"settings", "dj_settings", "django_settings"}
#: 契约访问器 → 契约缺省状态
ACCESSOR_KINDS = {"kernel_setting": "defaulted", "kernel_required_setting": "required"}


def _iter_kernel_modules():
    for path in sorted(KERNEL_DIR.rglob("*.py")):
        parts = set(path.parts)
        if "__pycache__" in parts or "migrations" in parts:
            continue
        if path == CONTRACT_MODULE:
            continue
        yield path


def scan_reads() -> dict[str, dict]:
    """扫描内核源码，返回 {键: {accessor: 访问器名集合, modules: 消费模块集合}}。"""
    found: dict[str, dict] = {}
    for path in _iter_kernel_modules():
        module = path.relative_to(KERNEL_DIR).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ACCESSOR_KINDS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                entry = found.setdefault(node.args[0].value, {"accessor": set(), "modules": set()})
                entry["accessor"].add(node.func.id)
                entry["modules"].add(module)
    return found


def scan_raw_reads() -> list[str]:
    """扫描内核源码的裸 settings 读取（未走契约访问器），返回 ``模块:行号 形态`` 清单。"""
    violations: list[str] = []
    for path in _iter_kernel_modules():
        module = path.relative_to(KERNEL_DIR).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "django.conf":
                if any(alias.name == "settings" for alias in node.names):
                    violations.append(f"{module}:{node.lineno} 直接 import django.conf.settings")
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in FORBIDDEN_SETTINGS_NAMES
                and node.attr.isupper()
            ):
                violations.append(f"{module}:{node.lineno} 裸读 {node.value.id}.{node.attr}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in FORBIDDEN_SETTINGS_NAMES
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                violations.append(f"{module}:{node.lineno} 裸读 getattr(..., {node.args[1].value!r})")
    return sorted(violations)


class TestAccessorOnly:
    def test_no_raw_settings_read_in_kernel(self):
        violations = scan_raw_reads()
        assert violations == [], "内核存在未走契约访问器的 settings 读取（契约面是唯一读取入口）：\n  " + "\n  ".join(
            violations
        )

    def test_contract_module_is_only_settings_importer(self):
        offenders = sorted(
            path.relative_to(KERNEL_DIR).as_posix()
            for path in _iter_kernel_modules()
            if "from django.conf import settings" in path.read_text(encoding="utf-8")
        )
        assert offenders == [], f"以下内核模块仍 import django.conf.settings：{offenders}"


class TestContractSurfaceLockstep:
    def test_every_kernel_settings_read_is_registered(self):
        reads = scan_reads()
        unregistered = sorted(set(reads) - set(KERNEL_SETTINGS))
        assert unregistered == [], f"内核读取了未登记的 settings 键：{unregistered}（请登记到 settings_contract.py）"

    def test_every_registered_key_is_actually_read(self):
        reads = scan_reads()
        dead = sorted(set(KERNEL_SETTINGS) - set(reads))
        assert dead == [], f"契约面登记了没有读取点的键：{dead}（防登记腐化，请核对后移除）"

    def test_accessor_form_matches_contract_default(self):
        reads = scan_reads()
        mismatched = []
        for name, entry in KERNEL_SETTINGS.items():
            expected = "kernel_required_setting" if entry.default is REQUIRED else "kernel_setting"
            accessors = reads[name]["accessor"]
            if accessors != {expected}:
                mismatched.append(f"{name}: 契约期望 {expected}，源码用 {sorted(accessors)}")
        assert mismatched == [], "访问器形态与契约缺省状态不一致：\n  " + "\n  ".join(mismatched)

    def test_consumers_match_scanned_modules(self):
        reads = scan_reads()
        mismatched = []
        for name, entry in KERNEL_SETTINGS.items():
            if set(entry.consumers) != reads[name]["modules"]:
                mismatched.append(f"{name}: 契约 {sorted(entry.consumers)} vs 源码 {sorted(reads[name]['modules'])}")
        assert mismatched == [], "消费方清单与源码不一致：\n  " + "\n  ".join(mismatched)

    def test_framework_flag_matches_django_builtins(self):
        """``framework=True`` ↔ 真正的 Django 内置键（``global_settings`` 恒有值），双向核对。"""
        marked = {name for name, entry in KERNEL_SETTINGS.items() if entry.framework}
        builtin = {name for name in KERNEL_SETTINGS if hasattr(global_settings, name)}
        assert marked == builtin, (
            f"framework 标记与 Django 内置键不一致：误标 {sorted(marked - builtin)}，漏标 {sorted(builtin - marked)}"
        )

    def test_required_helper_matches_contract(self):
        expected = tuple(sorted(name for name, entry in KERNEL_SETTINGS.items() if entry.default is REQUIRED))
        assert required_kernel_settings() == expected


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

    def test_kernel_setting_preserves_configured_container_identity(self):
        """宿主已配置时原样返回（就地改写 settings 生效的既有语义，如许可前缀追加）。"""
        provided = ["^/api/custom/"]
        with override_settings(PERMISSION_SHOW_PREFIX=provided):
            assert kernel_setting("PERMISSION_SHOW_PREFIX") is provided

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


class TestZeroConfigFallback:
    """零配置口径：有缺省的键缺失即按契约缺省值工作（宿主可省略全部非必给键）。"""

    def test_every_defaulted_key_falls_back_to_contract_default(self):
        with override_settings():
            mismatched = []
            for name, entry in KERNEL_SETTINGS.items():
                if entry.default is REQUIRED:
                    continue
                if hasattr(django_settings, name):
                    delattr(django_settings, name)
                value = kernel_setting(name)
                if value != entry.default or type(value) is not type(entry.default):
                    mismatched.append(f"{name}: 回落 {value!r} 与契约缺省 {entry.default!r} 不一致")
            assert mismatched == [], "零配置缺省回落与契约面不一致：\n  " + "\n  ".join(mismatched)

    def test_every_required_key_missing_raises_with_purpose(self):
        import pytest

        with override_settings():
            for name, entry in KERNEL_SETTINGS.items():
                if entry.default is not REQUIRED:
                    continue
                if hasattr(django_settings, name):
                    delattr(django_settings, name)
                with pytest.raises(ImproperlyConfigured) as exc:
                    kernel_setting(name)
                assert entry.purpose in str(exc.value), f"{name} 的必给报错缺少用途提示：{exc.value}"

    def test_mutable_fallback_is_isolated_copy(self):
        """回落缺省值必须是副本：调用方就地改写不得污染契约面共享对象。"""
        with override_settings():
            for name in (
                "PERMISSION_SHOW_PREFIX",
                "PERMISSION_DATA_AUTH_APPS",
                "PERMISSION_WHITE_URL",
                "API_MODEL_MAP",
            ):
                if hasattr(django_settings, name):
                    delattr(django_settings, name)
                fallback = kernel_setting(name)
                if isinstance(fallback, dict):
                    fallback["polluted"] = True
                else:
                    fallback.append("polluted")
                assert kernel_setting(name) == KERNEL_SETTINGS[name].default, f"{name} 的缺省对象被就地改写污染"
            assert copy.deepcopy(KERNEL_SETTINGS["PERMISSION_WHITE_URL"].default) == {}


class TestZeroConfigBoot:
    """「宿主能跑即配」冒烟：删掉全部非必给键后，代表性内核链路仍按契约缺省工作。"""

    @staticmethod
    def _drop_optional_settings():
        for name, entry in KERNEL_SETTINGS.items():
            if entry.default is not REQUIRED and hasattr(django_settings, name):
                delattr(django_settings, name)

    def test_representative_paths_run_on_contract_defaults(self):
        from common.base.decorators import _diagnostics_enabled
        from common.core.atomic_read import skip_atomic_enabled
        from common.core.middleware import ApiLoggingMiddleware
        from common.utils.verify_code import SendAndVerifyCodeUtil

        with override_settings():
            self._drop_optional_settings()

            assert _diagnostics_enabled() is False
            assert skip_atomic_enabled() is True

            middleware = ApiLoggingMiddleware(lambda request: None)
            assert middleware.enable is False
            assert middleware.methods == set()
            assert middleware.ignores == {}

            code_util = SendAndVerifyCodeUtil(target="13800000000", dryrun=True)
            assert code_util.timeout == KERNEL_SETTINGS["VERIFY_CODE_TTL"].default
            assert code_util.limit == KERNEL_SETTINGS["VERIFY_CODE_LIMIT"].default


class TestDocLockstep:
    """docs/architecture/kernel-package.md §三 契约表 / 宿主最小对接面须与本模块同源。"""

    @staticmethod
    def _section() -> str:
        text = DOC.read_text(encoding="utf-8")
        section = text.split("## 三、内核 settings 契约", 1)
        assert len(section) == 2, "内核 settings 契约章节缺失（## 三、内核 settings 契约）"
        return section[1]

    @classmethod
    def _rows(cls) -> dict[str, tuple[str, str]]:
        rows: dict[str, tuple[str, str]] = {}
        for line in cls._section().splitlines():
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

    def test_doc_minimal_host_surface_matches_required_keys(self):
        """「宿主最小对接面」= REQUIRED 键去除 Django 内置键，逐行登记在文档中。"""
        expected = sorted(name for name in required_kernel_settings() if not KERNEL_SETTINGS[name].framework)
        section = self._section()
        block = section.split("宿主最小对接面", 1)
        assert len(block) == 2, "文档缺少「宿主最小对接面」小节"
        listed = sorted(set(re.findall(r"^- `([A-Z][A-Z0-9_]*)`", block[1].split("## 四", 1)[0], re.M)))
        assert listed == expected, f"宿主最小对接面与契约不一致：文档 {listed} vs 契约 {expected}"
