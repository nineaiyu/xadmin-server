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
    monkeypatch.setattr(module, "CONTRACTS_MODULE", "packages/xadmin-common/common/contracts.py")
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
        # ops/server 在 SCAN_DIRS 里但不是业务 app：其中的模块级跨 import 不判违例
        write(tmp_path, "ops/helper.py", "from beta.models import Book\n")
        assert xcai.scan() == []

    def test_migrations_and_tests_excluded(self, xcai, tmp_path):
        write(tmp_path, "alpha/migrations/0001_initial.py", "from beta.models import Book\n")
        write(tmp_path, "alpha/tests/test_x.py", "from beta.models import Book\n")
        assert xcai.scan() == []

    def test_import_module_form_flagged(self, xcai, tmp_path):
        write(tmp_path, "alpha/views/z.py", "import beta.serializers as bs\n")
        assert len(xcai.scan()) == 1


class TestFrameworkDirection:
    """common（框架层）→ 业务 app 单缝收敛：唯一出口 packages/xadmin-common/common/contracts.py。"""

    FRAMEWORK_FILE = "packages/xadmin-common/common/foo.py"

    def test_direct_model_import_violation(self, xcai, tmp_path):
        write(tmp_path, self.FRAMEWORK_FILE, "from system.models import UserInfo\n")
        violations, _ = xcai.scan_framework_direction()
        assert any("须统一经 packages/xadmin-common/common/contracts.py" in msg for _, _, msg in violations)

    def test_services_import_outside_contracts_flagged_even_if_registered(self, xcai, tmp_path):
        # 单缝规则的 precedence：缝登记只对 contracts.py 生效，其余文件登记了也违例
        write(tmp_path, self.FRAMEWORK_FILE, "from system.services import getSomething\n")
        xcai.CONTRACT_SEAMS.update({self.FRAMEWORK_FILE: {"system.services": "历史登记"}})
        violations, _ = xcai.scan_framework_direction()
        assert any("须统一经 packages/xadmin-common/common/contracts.py" in msg for _, _, msg in violations)

    def test_contracts_registered_seam_passes(self, xcai, tmp_path):
        write(
            tmp_path,
            "packages/xadmin-common/common/contracts.py",
            '_CONTRACT_PROVIDERS = {\n    "X": ("system.services", "原因"),\n}\n',
        )
        xcai.CONTRACT_SEAMS.update({"packages/xadmin-common/common/contracts.py": {"system.services": "测试缝"}})
        violations, _ = xcai.scan_framework_direction()
        assert violations == []

    def test_contracts_unregistered_seam_violation(self, xcai, tmp_path):
        write(
            tmp_path,
            "packages/xadmin-common/common/contracts.py",
            '_CONTRACT_PROVIDERS = {\n    "X": ("system.services", "原因"),\n}\n',
        )
        violations, _ = xcai.scan_framework_direction()
        assert any("未登记的契约缝 system.services" in msg for _, _, msg in violations)

    def test_contracts_registered_seam_drift_detected(self, xcai, tmp_path):
        # 台账登记了缝但白名单里已无该提供方：双向漂移必须报
        write(
            tmp_path,
            "packages/xadmin-common/common/contracts.py",
            '_CONTRACT_PROVIDERS = {"X": ("system.services", "r")}\n',
        )
        xcai.CONTRACT_SEAMS.update({"packages/xadmin-common/common/contracts.py": {"approval.services": "已迁移的缝"}})
        violations, _ = xcai.scan_framework_direction()
        assert any("登记的契约缝 approval.services 已不存在" in msg for _, _, msg in violations)

    def test_contracts_module_level_import_still_shape_checked(self, xcai, tmp_path):
        # contracts.py 自身的模块级 import 仍受「仅 *.services」+ 登记约束
        write(tmp_path, "packages/xadmin-common/common/contracts.py", "from system.models import UserInfo\n")
        violations, _ = xcai.scan_framework_direction()
        assert any("框架层须经" in msg for _, _, msg in violations)

    def test_lazy_import_is_observation_not_violation(self, xcai, tmp_path):
        write(
            tmp_path,
            self.FRAMEWORK_FILE,
            "import os\n\n\ndef f():\n    from system.services import getSomething\n    return getSomething\n",
        )
        violations, observations = xcai.scan_framework_direction()
        assert violations == []
        assert observations == {self.FRAMEWORK_FILE: {"system.services"}}

    def test_non_business_app_import_ignored(self, xcai, tmp_path):
        write(tmp_path, self.FRAMEWORK_FILE, "from os.path import join\nimport collections\n")
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


# ---------------------------------------------------------------------------
# check_function_length.py
# ---------------------------------------------------------------------------


@pytest.fixture
def cfln(tmp_path, monkeypatch):
    module = load_gate("cfln_gate_under_test", "check_function_length")
    (tmp_path / "common").mkdir()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SCAN_DIRS", ["common"])
    monkeypatch.setattr(module, "BASELINE", {})
    monkeypatch.setattr(module, "THRESHOLD", 100)
    return module


def _long_func(name: str, lines: int) -> str:
    """生成正好 lines 行（含 def 行）的函数源码。"""
    body = "\n".join(f"    x{i} = {i}" for i in range(lines - 2))
    return f"def {name}():\n{body}\n    return 0\n"


class TestFunctionLength:
    def test_ast_counts_exact_function_span(self, cfln, tmp_path):
        write(tmp_path, "common/a.py", _long_func("big", 100))
        rows = cfln.collect()
        assert [(rel, name, size) for rel, name, size in rows] == [("common/a.py", "big", 100)]

    def test_nested_and_method_qualname(self, cfln, tmp_path):
        write(
            tmp_path,
            "common/a.py",
            "class A:\n" + "\n".join("    " + line for line in _long_func("big", 100).splitlines()),
        )
        assert [(name, size) for _rel, name, size in cfln.collect()] == [("A.big", 100)]

    def test_below_threshold_not_collected(self, cfln, tmp_path):
        write(tmp_path, "common/a.py", _long_func("ok", 99))
        assert cfln.collect() == []

    def test_new_oversized_function_fails(self, cfln, tmp_path, capsys):
        write(tmp_path, "common/a.py", _long_func("big", 101))
        original = sys.argv
        sys.argv = ["check_function_length.py"]
        try:
            assert cfln.main() == 1
        finally:
            sys.argv = original
        assert "未登记的新增超长函数" in capsys.readouterr().out

    def test_baseline_only_shrink_never_grow(self, cfln, tmp_path, capsys):
        write(tmp_path, "common/a.py", _long_func("big", 103))
        cfln.BASELINE.update({"common/a.py::big": 105})
        original = sys.argv
        sys.argv = ["check_function_length.py"]
        try:
            assert cfln.main() == 0  # 103 <= 基线 105：放行
            capsys.readouterr()
            write(tmp_path, "common/a.py", _long_func("big", 106))
            assert cfln.main() == 1  # 超基线：阻断
            assert "只减不增" in capsys.readouterr().out
        finally:
            sys.argv = original

    def test_migrations_and_tests_excluded(self, cfln, tmp_path):
        write(tmp_path, "common/migrations/0001_x.py", _long_func("big", 101))
        write(tmp_path, "common/tests/test_x.py", _long_func("big", 101))
        assert cfln.collect() == []


# ---------------------------------------------------------------------------
# check_doc_size.py
# ---------------------------------------------------------------------------


@pytest.fixture
def cds(tmp_path, monkeypatch):
    module = load_gate("cds_gate_under_test", "check_doc_size")
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "DOC_SIZE_BUDGET_KB", 40)
    monkeypatch.setattr(module, "DOC_SIZE_EXEMPT_PREFIXES", {})
    monkeypatch.setattr(module, "DOC_SIZE_EXEMPT", {})
    return module


def _run_main(module, *argv):
    original = sys.argv
    sys.argv = ["gate.py", *argv]
    try:
        return module.main()
    finally:
        sys.argv = original


class TestDocSize:
    def test_collect_recursive_markdown_only(self, cds, tmp_path):
        write(tmp_path, "docs/a.md", "x")
        write(tmp_path, "docs/sub/b.md", "y")
        write(tmp_path, "docs/c.txt", "z")
        assert [rel for rel, _ in cds.collect()] == ["docs/a.md", "docs/sub/b.md"]

    def test_new_oversized_doc_fails(self, cds, tmp_path, capsys):
        write(tmp_path, "docs/big.md", "x" * (41 * 1024))
        assert _run_main(cds) == 1
        assert "未登记的新增超预算文档" in capsys.readouterr().out

    def test_within_budget_passes(self, cds, tmp_path, capsys):
        write(tmp_path, "docs/ok.md", "x" * (39 * 1024))
        assert _run_main(cds) == 0
        assert "文档体积门禁通过" in capsys.readouterr().out

    def test_prefix_exempt_dir_passes(self, cds, tmp_path):
        cds.DOC_SIZE_EXEMPT_PREFIXES.update({"docs/plans/archive/": "只读历史归档"})
        write(tmp_path, "docs/plans/archive/big.md", "x" * (60 * 1024))
        assert _run_main(cds) == 0

    def test_per_file_exempt_only_shrink(self, cds, tmp_path, capsys):
        cds.DOC_SIZE_EXEMPT.update({"docs/big.md": {"reason": "承接中", "baseline_kb": 45}})
        write(tmp_path, "docs/big.md", "x" * (44 * 1024))
        assert _run_main(cds) == 0  # 44 <= 基线 45：放行
        capsys.readouterr()
        write(tmp_path, "docs/big.md", "x" * (46 * 1024))
        assert _run_main(cds) == 1  # 超基线：阻断
        assert "只减不增" in capsys.readouterr().out

    def test_report_mode_never_fails(self, cds, tmp_path):
        write(tmp_path, "docs/big.md", "x" * (50 * 1024))
        assert _run_main(cds, "--report") == 0


# ---------------------------------------------------------------------------
# check_adr_status.py
# ---------------------------------------------------------------------------


@pytest.fixture
def cas(tmp_path):
    module = load_gate("cas_gate_under_test", "check_adr_status")
    return module


def _write_adr(tmp_path: Path, name: str, status_value: str, body: str = "") -> None:
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True, exist_ok=True)
    (adr / name).write_text(f"# {name[:7]}\n\n- 状态：{status_value}\n\n## 背景\n{body}\n", encoding="utf-8")


class TestAdrStatus:
    def test_normalized_status_passes(self, cas, tmp_path):
        _write_adr(tmp_path, "ADR-001-a.md", "已交付")
        _write_adr(tmp_path, "ADR-002-b.md", "已接受（2026-09-04）")
        violations, counts, _ = cas.collect_violations(tmp_path)
        assert violations == []
        assert counts["已交付"] == 1
        assert counts["已接受"] == 1

    def test_missing_status_line_reported(self, cas, tmp_path):
        adr = tmp_path / "docs" / "adr"
        adr.mkdir(parents=True)
        (adr / "ADR-001-a.md").write_text("# ADR-001\n\n## 背景\n", encoding="utf-8")
        violations, _, _ = cas.collect_violations(tmp_path)
        assert any("未找到状态行" in item for item in violations)

    def test_status_outside_closed_set_reported(self, cas, tmp_path):
        _write_adr(tmp_path, "ADR-001-a.md", "进行中")
        violations, _, _ = cas.collect_violations(tmp_path)
        assert any("不在规范闭集" in item for item in violations)

    def test_superseded_missing_target_reported(self, cas, tmp_path):
        _write_adr(tmp_path, "ADR-001-a.md", "被取代（Superseded by ADR-002）")
        violations, counts, _ = cas.collect_violations(tmp_path)
        assert counts["被取代"] == 1
        assert any("不存在" in item for item in violations)

    def test_superseded_chain_ok(self, cas, tmp_path):
        _write_adr(tmp_path, "ADR-001-a.md", "被取代（Superseded by ADR-002）")
        _write_adr(tmp_path, "ADR-002-b.md", "已交付", body="本决策整体取代 ADR-001（原方案弃用）。")
        violations, _, _ = cas.collect_violations(tmp_path)
        assert violations == []

    def test_superseded_bidirectional_note_required(self, cas, tmp_path):
        _write_adr(tmp_path, "ADR-001-a.md", "被取代（Superseded by ADR-002）")
        _write_adr(tmp_path, "ADR-002-b.md", "已交付")
        violations, _, _ = cas.collect_violations(tmp_path)
        assert any("双向一致" in item for item in violations)

    def test_trigger_required_for_deferred(self, cas, tmp_path):
        _write_adr(tmp_path, "ADR-001-a.md", "暂不实施（触发制登记）")
        violations, _, _ = cas.collect_violations(tmp_path)
        assert any("触发制任务清单" in item for item in violations)
        write(tmp_path, "docs/plans/触发制任务清单-长期.md", "| 项 | [ADR-001](../adr/ADR-001-a.md) 触发制 |\n")
        violations, _, _ = cas.collect_violations(tmp_path)
        assert violations == []

    def test_readme_status_lockstep(self, cas, tmp_path):
        _write_adr(tmp_path, "ADR-001-a.md", "已交付")
        write(
            tmp_path,
            "docs/adr/README.md",
            "| ADR | 状态 | 主题 |\n|-----|------|------|\n| [ADR-001](ADR-001-a.md) | 已接受 | x |\n",
        )
        violations, _, _ = cas.collect_violations(tmp_path)
        assert any("与文件内状态" in item for item in violations)
