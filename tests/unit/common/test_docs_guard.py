# -*- coding: utf-8 -*-
"""文档守护脚本测试（索引覆盖 / 权威源路径 / 教程镜像）。

1. 当前仓库三脚本全绿（本地 pytest 同样能抓到文档漂移，不只依赖 CI wiring）；
2. 各脚本的校验逻辑：违例可报出（防校验器退化成静默 no-op）+ 正向用例可通过。
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_doc_index = _load_script("check_doc_index")
check_doc_paths = _load_script("check_doc_paths")
check_tutorial_mirror = _load_script("check_tutorial_mirror")
check_docs_site_nav = _load_script("check_docs_site_nav")


class TestRepoState:
    def test_doc_index_clean(self):
        assert check_doc_index.collect_violations(REPO_ROOT) == []

    def test_doc_paths_reachable(self):
        assert check_doc_paths.collect_violations(REPO_ROOT) == []

    def test_tutorial_mirror_clean(self):
        assert check_tutorial_mirror.collect_violations(REPO_ROOT) == []

    def test_docs_site_nav_clean(self):
        assert check_docs_site_nav.collect_violations() == []


class TestDocIndexLogic:
    def test_unregistered_doc_is_reported(self, tmp_path):
        architecture = tmp_path / "docs" / "architecture"
        architecture.mkdir(parents=True)
        (architecture / "orphan.md").write_text("x", encoding="utf-8")
        (tmp_path / "docs" / "README.md").write_text("# index\n", encoding="utf-8")
        violations = check_doc_index.collect_violations(tmp_path, client_root=tmp_path / "none")
        assert len(violations) == 1
        assert "orphan.md" in violations[0]

    def test_registered_doc_passes(self, tmp_path):
        architecture = tmp_path / "docs" / "architecture"
        architecture.mkdir(parents=True)
        (architecture / "wired.md").write_text("x", encoding="utf-8")
        (tmp_path / "docs" / "README.md").write_text("| wired.md | ok |\n", encoding="utf-8")
        assert check_doc_index.collect_violations(tmp_path, client_root=tmp_path / "none") == []

    def test_dir_readme_registration_is_honored(self, tmp_path):
        plans = tmp_path / "docs" / "plans"
        plans.mkdir(parents=True)
        (plans / "plan.md").write_text("x", encoding="utf-8")
        (plans / "README.md").write_text("| plan.md | ok |\n", encoding="utf-8")
        (tmp_path / "docs" / "README.md").write_text("# index\n", encoding="utf-8")
        assert check_doc_index.collect_violations(tmp_path, client_root=tmp_path / "none") == []


class TestDocPathsLogic:
    def test_missing_source_path_is_reported(self, tmp_path):
        architecture = tmp_path / "docs" / "architecture"
        architecture.mkdir(parents=True)
        (architecture / "component-handbook.md").write_text("- 权威源：`common/nonexistent.py`。\n", encoding="utf-8")
        violations = check_doc_paths.collect_violations(tmp_path, client_root=tmp_path / "none")
        assert len(violations) == 1
        assert "common/nonexistent.py" in violations[0]

    def test_braces_expansion_and_existing_path(self, tmp_path):
        architecture = tmp_path / "docs" / "architecture"
        architecture.mkdir(parents=True)
        common = tmp_path / "common"
        common.mkdir()
        (common / "a.py").write_text("", encoding="utf-8")
        (common / "b.py").write_text("", encoding="utf-8")
        (architecture / "component-handbook.md").write_text("- 权威源：`common/{a.py,b.py}`。\n", encoding="utf-8")
        assert check_doc_paths.collect_violations(tmp_path, client_root=tmp_path / "none") == []

    def test_non_path_tokens_are_ignored(self, tmp_path):
        architecture = tmp_path / "docs" / "architecture"
        architecture.mkdir(parents=True)
        (architecture / "component-handbook.md").write_text(
            "- 权威源：`common/{a.py,b.py}`（`RePlusPageProps`）。\n", encoding="utf-8"
        )
        common = tmp_path / "common"
        common.mkdir()
        (common / "a.py").write_text("", encoding="utf-8")
        (common / "b.py").write_text("", encoding="utf-8")
        assert check_doc_paths.collect_violations(tmp_path, client_root=tmp_path / "none") == []

    def test_active_doc_missing_code_path_is_reported(self, tmp_path):
        architecture = tmp_path / "docs" / "architecture"
        architecture.mkdir(parents=True)
        (architecture / "component-handbook.md").write_text("（空手册）\n", encoding="utf-8")
        (architecture / "some-doc.md").write_text("见 `common/core/nothere.py`。\n", encoding="utf-8")
        violations = check_doc_paths.collect_violations(
            tmp_path, client_root=tmp_path / "none", docs_root=tmp_path / "none"
        )
        assert len(violations) == 1
        assert "common/core/nothere.py" in violations[0]

    def test_active_doc_existing_code_path_passes(self, tmp_path):
        architecture = tmp_path / "docs" / "architecture"
        architecture.mkdir(parents=True)
        (architecture / "component-handbook.md").write_text("（空手册）\n", encoding="utf-8")
        (architecture / "some-doc.md").write_text("见 `common/core/models.py::Book`。\n", encoding="utf-8")
        core = tmp_path / "common" / "core"
        core.mkdir(parents=True)
        (core / "models.py").write_text("", encoding="utf-8")
        violations = check_doc_paths.collect_violations(
            tmp_path, client_root=tmp_path / "none", docs_root=tmp_path / "none"
        )
        assert violations == []


class TestTutorialMirrorLogic:
    def _write_doc(self, tmp_path, content: str):
        guide = tmp_path / "docs" / "guide"
        guide.mkdir(parents=True, exist_ok=True)
        (guide / "first-module-30min.md").write_text(content, encoding="utf-8")

    def _write_generate_crud_command(self, tmp_path):
        commands = tmp_path / "common" / "management" / "commands"
        commands.mkdir(parents=True, exist_ok=True)
        (commands / "generate_crud.py").write_text(
            'parser.add_argument("--dry-run", action="store_true")\n', encoding="utf-8"
        )

    def test_known_param_passes(self, tmp_path):
        self._write_doc(tmp_path, "python manage.py generate_crud demo.Book --dry-run\n")
        self._write_generate_crud_command(tmp_path)
        violations = check_tutorial_mirror.collect_violations(
            tmp_path, client_root=tmp_path / "none", docs_root=tmp_path / "none"
        )
        assert violations == []

    def test_unknown_param_is_reported(self, tmp_path):
        self._write_doc(tmp_path, "python manage.py generate_crud demo.Book --not-a-param\n")
        self._write_generate_crud_command(tmp_path)
        violations = check_tutorial_mirror.collect_violations(
            tmp_path, client_root=tmp_path / "none", docs_root=tmp_path / "none"
        )
        assert len(violations) == 1
        assert "--not-a-param" in violations[0]

    def test_unknown_command_is_reported(self, tmp_path):
        self._write_doc(tmp_path, "python manage.py not_a_command\n")
        violations = check_tutorial_mirror.collect_violations(
            tmp_path, client_root=tmp_path / "none", docs_root=tmp_path / "none"
        )
        assert any("not_a_command" in item for item in violations)

    def test_missing_demo_path_is_reported(self, tmp_path):
        self._write_doc(tmp_path, "模型在 `demo/models.py` 里定义\n")
        violations = check_tutorial_mirror.collect_violations(
            tmp_path, client_root=tmp_path / "none", docs_root=tmp_path / "none"
        )
        assert len(violations) == 1
        assert "demo/models.py" in violations[0]


class TestDocsSiteNavLogic:
    def _write_config(self, tmp_path, links):
        vitepress = tmp_path / ".vitepress"
        vitepress.mkdir(parents=True, exist_ok=True)
        body = ", ".join(f"{{ text: 'x', link: '{link}' }}" for link in links)
        (vitepress / "config.mts").write_text(f"export default {{ nav: [{body}] }}\n", encoding="utf-8")

    def test_orphan_page_is_reported(self, tmp_path):
        guide = tmp_path / "guide"
        guide.mkdir()
        (guide / "index.md").write_text("x", encoding="utf-8")
        (guide / "orphan.md").write_text("x", encoding="utf-8")
        self._write_config(tmp_path, ["/guide/index"])
        violations = check_docs_site_nav.collect_violations(tmp_path)
        assert len(violations) == 1
        assert "orphan.md" in violations[0]

    def test_registered_page_passes(self, tmp_path):
        guide = tmp_path / "guide"
        guide.mkdir()
        (guide / "wired.md").write_text("x", encoding="utf-8")
        self._write_config(tmp_path, ["/guide/wired"])
        assert check_docs_site_nav.collect_violations(tmp_path) == []

    def test_index_dir_link_is_honored(self, tmp_path):
        guide = tmp_path / "guide"
        guide.mkdir()
        (guide / "index.md").write_text("x", encoding="utf-8")
        self._write_config(tmp_path, ["/guide"])
        assert check_docs_site_nav.collect_violations(tmp_path) == []

    def test_root_page_without_nav_is_reported(self, tmp_path):
        (tmp_path / "donate.md").write_text("x", encoding="utf-8")
        self._write_config(tmp_path, ["/"])
        violations = check_docs_site_nav.collect_violations(tmp_path)
        assert len(violations) == 1
        assert "donate.md" in violations[0]

    def test_missing_config_is_skipped(self, tmp_path):
        guide = tmp_path / "guide"
        guide.mkdir()
        (guide / "a.md").write_text("x", encoding="utf-8")
        assert check_docs_site_nav.collect_violations(tmp_path) == []
