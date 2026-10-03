# -*- coding: utf-8 -*-
"""CI 静态门禁脚本自身的行为测试。

门禁脚本是 CI 的「裁判」：判定逻辑出错时全体假绿/假红且无人察觉，因此其
关键分支（违例识别、allowlist 豁免、方向规则、台账双向漂移、基线只减不增）
必须有自己的守护用例。脚本不是包，经 importlib 按路径加载，用 tmp_path
构造迷你仓库树并 monkeypatch 模块级 REPO_ROOT/APPS 等常量，保持用例封闭。
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"


def load_gate(module_name: str, script_name: str):
    """按路径加载 scripts/ 下的门禁脚本为独立模块（脚本无包结构）。"""
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{script_name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# check_cross_app_imports.py
# ---------------------------------------------------------------------------


@pytest.fixture
def xcai(tmp_path, monkeypatch):
    """加载跨 app 门禁脚本并把扫描根指向 tmp 仓库树（默认 alpha/beta 两 app）。"""
    module = load_gate("xcai_gate_under_test", "check_cross_app_imports")
    for dirname in ("alpha", "beta"):
        (tmp_path / dirname).mkdir()
        (tmp_path / dirname / "apps.py").write_text("")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    # system 纳入 APPS：框架层方向规则的用例需要它作为「业务 app」参与判定
    monkeypatch.setattr(module, "APPS", {"alpha", "beta", "system"})
    monkeypatch.setattr(module, "SCAN_DIRS", ["alpha", "beta"])
    monkeypatch.setattr(module, "ALLOWLIST", {})
    monkeypatch.setattr(module, "CONTRACT_SEAMS", {})
    # 单缝出口：路径名判定，与 tmp 树中是否真有该文件无关
    monkeypatch.setattr(module, "CONTRACTS_MODULE", "common/contracts.py")
    return module


def write(tmp_path: Path, rel: str, text: str) -> None:
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class TestCrossAppImports:
    def test_discover_apps_by_apps_py_convention(self, xcai, tmp_path):
        (tmp_path / "gamma").mkdir()
        (tmp_path / "gamma" / "apps.py").write_text("")
        (tmp_path / "plain").mkdir()
        assert xcai.discover_apps() == {"alpha", "beta", "gamma"}

    def test_cross_app_module_import_flagged(self, xcai, tmp_path):
        write(tmp_path, "alpha/views/x.py", "from beta.models import Book\n")
        violations = xcai.scan()
        assert len(violations) == 1
        rel, line, stmt = violations[0]
        assert rel == "alpha/views/x.py"
        assert line == 1
        assert "from beta.models" in stmt  # group(0) 截至模块关键字，不含导入名

    def test_same_app_and_function_level_import_allowed(self, xcai, tmp_path):
        write(
            tmp_path,
            "alpha/views/y.py",
            "from alpha.models import Book\n\n\ndef f():\n    from beta.models import Other\n    return Other\n",
        )
        assert xcai.scan() == []

    def test_allowlist_entry_skipped(self, xcai, tmp_path):
        write(tmp_path, "alpha/views/x.py", "from beta.models import Book\n")
        monkey_target = xcai
        monkey_target.ALLOWLIST.update({"alpha/views/x.py": "演示原因"})
        assert xcai.scan() == []

    def test_non_app_source_dirs_not_scanned(self, xcai, tmp_path):
        # utils/server 在 SCAN_DIRS 里但不是业务 app：其中的模块级跨 import 不判违例
        write(tmp_path, "utils/helper.py", "from beta.models import Book\n")
        assert xcai.scan() == []

    def test_migrations_and_tests_excluded(self, xcai, tmp_path):
        write(tmp_path, "alpha/migrations/0001_initial.py", "from beta.models import Book\n")
        write(tmp_path, "alpha/tests/test_x.py", "from beta.models import Book\n")
        assert xcai.scan() == []

    def test_import_module_form_flagged(self, xcai, tmp_path):
        write(tmp_path, "alpha/views/z.py", "import beta.serializers as bs\n")
        assert len(xcai.scan()) == 1


class TestFrameworkDirection:
    """common（框架层）→ 业务 app 单缝收敛：唯一出口 common/contracts.py。"""

    def test_direct_model_import_violation(self, xcai, tmp_path):
        write(tmp_path, "common/foo.py", "from system.models import UserInfo\n")
        violations, _ = xcai.scan_framework_direction()
        assert any("须统一经 common/contracts.py" in msg for _, _, msg in violations)

    def test_services_import_outside_contracts_flagged_even_if_registered(self, xcai, tmp_path):
        # 单缝规则的 precedence：缝登记只对 contracts.py 生效，其余文件登记了也违例
        write(tmp_path, "common/foo.py", "from system.services import getSomething\n")
        xcai.CONTRACT_SEAMS.update({"common/foo.py": {"system.services": "历史登记"}})
        violations, _ = xcai.scan_framework_direction()
        assert any("须统一经 common/contracts.py" in msg for _, _, msg in violations)

    def test_contracts_registered_seam_passes(self, xcai, tmp_path):
        write(
            tmp_path,
            "common/contracts.py",
            '_CONTRACT_PROVIDERS = {\n    "X": ("system.services", "原因"),\n}\n',
        )
        xcai.CONTRACT_SEAMS.update({"common/contracts.py": {"system.services": "测试缝"}})
        violations, _ = xcai.scan_framework_direction()
        assert violations == []

    def test_contracts_unregistered_seam_violation(self, xcai, tmp_path):
        write(
            tmp_path,
            "common/contracts.py",
            '_CONTRACT_PROVIDERS = {\n    "X": ("system.services", "原因"),\n}\n',
        )
        violations, _ = xcai.scan_framework_direction()
        assert any("未登记的契约缝 system.services" in msg for _, _, msg in violations)

    def test_contracts_registered_seam_drift_detected(self, xcai, tmp_path):
        # 台账登记了缝但白名单里已无该提供方：双向漂移必须报
        write(tmp_path, "common/contracts.py", '_CONTRACT_PROVIDERS = {"X": ("system.services", "r")}\n')
        xcai.CONTRACT_SEAMS.update({"common/contracts.py": {"approval.services": "已迁移的缝"}})
        violations, _ = xcai.scan_framework_direction()
        assert any("登记的契约缝 approval.services 已不存在" in msg for _, _, msg in violations)

    def test_contracts_module_level_import_still_shape_checked(self, xcai, tmp_path):
        # contracts.py 自身的模块级 import 仍受「仅 *.services」+ 登记约束
        write(tmp_path, "common/contracts.py", "from system.models import UserInfo\n")
        violations, _ = xcai.scan_framework_direction()
        assert any("框架层须经" in msg for _, _, msg in violations)

    def test_lazy_import_is_observation_not_violation(self, xcai, tmp_path):
        write(
            tmp_path,
            "common/foo.py",
            "import os\n\n\ndef f():\n    from system.services import getSomething\n    return getSomething\n",
        )
        violations, observations = xcai.scan_framework_direction()
        assert violations == []
        assert observations == {"common/foo.py": {"system.services"}}

    def test_non_business_app_import_ignored(self, xcai, tmp_path):
        write(tmp_path, "common/foo.py", "from os.path import join\nimport collections\n")
        violations, observations = xcai.scan_framework_direction()
        assert violations == []
        assert observations == {}

    def test_seam_module_from_match(self, xcai):
        monkeypatch_target = xcai
        monkeypatch_target.APPS = {"alpha", "beta", "system", "approval", "common"}
        parse = lambda text: xcai._seam_module_from_match(  # noqa: E731
            xcai.SEAM_IMPORT_PATTERN.search(text)
        )
        assert parse("from system.services import x") == ("system", "system.services")
        assert parse("from system.tasks.helpers import x") == ("system", "system.tasks.helpers")
        assert parse("import approval.tasks") == ("approval", "approval.tasks")
        # common 是框架层自身：不构成缝
        assert parse("from common.models import x") is None
        # 非业务 app（三方/stdlib）：不构成缝
        assert parse("from os.path import join") is None

    def test_contract_provider_regex(self, xcai):
        text = (
            "_CONTRACT_PROVIDERS: dict[str, tuple[str, str]] = {\n"
            '    "SystemConfig": ("system.services", "原因"),\n'
            '    "process_approval": ("approval.services", "原因"),\n'
            "}\n"
        )
        assert set(xcai.CONTRACT_PROVIDER_RE.findall(text)) == {"system.services", "approval.services"}

    def test_main_exit_codes(self, xcai, tmp_path, capsys):
        write(tmp_path, "alpha/views/x.py", "from beta.models import Book\n")
        assert xcai.main() == 1
        (tmp_path / "alpha/views/x.py").write_text("from alpha.models import Book\n", encoding="utf-8")
        assert xcai.main() == 0
        assert "跨 app import 门禁通过" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# check_file_length.py
# ---------------------------------------------------------------------------


@pytest.fixture
def cfl(tmp_path, monkeypatch):
    module = load_gate("cfl_gate_under_test", "check_file_length")
    (tmp_path / "common").mkdir()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SCAN_DIRS", ["common"])
    monkeypatch.setattr(module, "BASELINE", {})
    monkeypatch.setattr(module, "THRESHOLD", 500)
    return module


class TestFileLength:
    def test_iter_sources_excludes_non_source_dirs(self, cfl, tmp_path):
        write(tmp_path, "common/a.py", "x = 1\n")
        write(tmp_path, "common/migrations/0001.py", "x = 1\n")
        write(tmp_path, "common/tests/test_a.py", "x = 1\n")
        write(tmp_path, "common/__pycache__/a.cpython.pyc", "")
        assert [p.name for p in cfl.iter_sources()] == ["a.py"]

    def test_count_lines(self, cfl, tmp_path):
        write(tmp_path, "common/a.py", "a\nb\nc\n")
        assert cfl.count_lines(tmp_path / "common/a.py") == 3

    def test_new_oversized_file_fails(self, cfl, tmp_path, capsys):
        write(tmp_path, "common/big.py", "\n" * 501)
        monkeypatch_argv = ["check_file_length.py"]
        original = sys.argv
        sys.argv = monkeypatch_argv
        try:
            assert cfl.main() == 1
        finally:
            sys.argv = original
        assert "未登记的新增巨型文件" in capsys.readouterr().out

    def test_baseline_only_shrink_never_grow(self, cfl, tmp_path, capsys):
        write(tmp_path, "common/big.py", "\n" * 503)
        cfl.BASELINE.update({"common/big.py": 505})
        original = sys.argv
        sys.argv = ["check_file_length.py"]
        try:
            assert cfl.main() == 0  # 503 <= 基线 505：放行
            capsys.readouterr()
            (tmp_path / "common/big.py").write_text("\n" * 506, encoding="utf-8")
            assert cfl.main() == 1  # 超基线：阻断
            assert "只减不增" in capsys.readouterr().out
        finally:
            sys.argv = original

    def test_report_mode_never_fails(self, cfl, tmp_path):
        write(tmp_path, "common/big.py", "\n" * 501)
        original = sys.argv
        sys.argv = ["check_file_length.py", "--report"]
        try:
            assert cfl.main() == 0
        finally:
            sys.argv = original

    def test_all_within_threshold_passes(self, cfl, tmp_path, capsys):
        write(tmp_path, "common/ok.py", "\n" * 499)
        original = sys.argv
        sys.argv = ["check_file_length.py"]
        try:
            assert cfl.main() == 0
        finally:
            sys.argv = original
        assert "巨型文件门禁通过" in capsys.readouterr().out
