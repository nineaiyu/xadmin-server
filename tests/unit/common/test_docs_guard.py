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
check_docs_site_links = _load_script("check_docs_site_links")


class TestRepoState:
    def test_doc_index_clean(self):
        assert check_doc_index.collect_violations(REPO_ROOT) == []

    def test_doc_paths_reachable(self):
        assert check_doc_paths.collect_violations(REPO_ROOT) == []

    def test_tutorial_mirror_clean(self):
        assert check_tutorial_mirror.collect_violations(REPO_ROOT) == []

    def test_docs_site_nav_clean(self):
        assert check_docs_site_nav.collect_violations() == []

    def test_docs_site_links_clean(self):
        # 跨仓缺失（单仓检出）或构建产物缺失时脚本自身走跳过分支，仍应无违例
        assert check_docs_site_links.collect_violations() == []


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


class TestArchiveGuard:
    """plans/archive/ 双向登记：逐篇登记 plans/README，且不得出现在 docs/README 主索引。"""

    def _seed(self, tmp_path, archive_names, plans_text="", main_text="# index\n"):
        archive = tmp_path / "docs" / "plans" / "archive"
        archive.mkdir(parents=True)
        for name in archive_names:
            (archive / name).write_text("x", encoding="utf-8")
        (tmp_path / "docs" / "plans" / "README.md").write_text(plans_text, encoding="utf-8")
        (tmp_path / "docs" / "README.md").write_text(main_text, encoding="utf-8")

    def test_unregistered_archive_reported(self, tmp_path):
        self._seed(tmp_path, ["old.md"])
        violations = check_doc_index.collect_archive_violations(tmp_path)
        assert len(violations) == 1
        assert "未在 plans/README.md" in violations[0]

    def test_registered_archive_passes(self, tmp_path):
        self._seed(tmp_path, ["old.md"], plans_text="| old.md | 说明 |\n")
        assert check_doc_index.collect_archive_violations(tmp_path) == []

    def test_archive_in_main_index_reported(self, tmp_path):
        self._seed(tmp_path, ["old.md"], plans_text="| old.md | 说明 |\n", main_text="# 索引\nold.md\n")
        violations = check_doc_index.collect_archive_violations(tmp_path)
        assert any("不得出现在 docs/README.md 主索引" in item for item in violations)

    def test_missing_archive_dir_skipped(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "README.md").write_text("# index\n", encoding="utf-8")
        assert check_doc_index.collect_archive_violations(tmp_path) == []


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
        (architecture / "some-doc.md").write_text(
            "见 `packages/xadmin-common/common/core/nothere.py`。\n", encoding="utf-8"
        )
        violations = check_doc_paths.collect_violations(
            tmp_path, client_root=tmp_path / "none", docs_root=tmp_path / "none"
        )
        assert len(violations) == 1
        assert "packages/xadmin-common/common/core/nothere.py" in violations[0]

    def test_active_doc_existing_code_path_passes(self, tmp_path):
        architecture = tmp_path / "docs" / "architecture"
        architecture.mkdir(parents=True)
        (architecture / "component-handbook.md").write_text("（空手册）\n", encoding="utf-8")
        (architecture / "some-doc.md").write_text(
            "见 `packages/xadmin-common/common/core/models.py::Book`。\n", encoding="utf-8"
        )
        core = tmp_path / "packages" / "xadmin-common" / "common" / "core"
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

    def test_nested_tests_demo_path_is_not_mistaken_for_app_path(self, tmp_path):
        self._write_doc(tmp_path, "uv run pytest tests/integration/demo/test_book_viewset.py -q\n")
        violations = check_tutorial_mirror.collect_violations(
            tmp_path, client_root=tmp_path / "none", docs_root=tmp_path / "none"
        )
        assert violations == []


class TestDocsSiteLinksLogic:
    """站点链接与引用可达性：锚点以构建产物为准，跨仓路径引用按归属仓解析。"""

    def _seed(self, tmp_path, rel: str, text: str):
        page = tmp_path / rel
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(text, encoding="utf-8")
        return page

    def _seed_dist(self, tmp_path, rel: str, ids):
        html = tmp_path / "dist" / rel
        html.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(f'<h2 id="{item}">x</h2>' for item in ids)
        html.write_text(f"<html><body>{body}</body></html>", encoding="utf-8")

    def _run(self, tmp_path, **kwargs):
        return check_docs_site_links.collect_violations(
            docs_root=tmp_path,
            dist_root=tmp_path / "dist",
            server_root=kwargs.get("server_root", tmp_path / "server_none"),
            installer_root=kwargs.get("installer_root", tmp_path / "installer_none"),
        )

    def test_missing_page_is_reported(self, tmp_path):
        self._seed(tmp_path, "guide/a.md", "[b](./no-such.md)\n")
        violations = self._run(tmp_path)
        assert len(violations) == 1
        assert "链接目标不存在" in violations[0] and "no-such.md" in violations[0]

    def test_anchor_hit_passes(self, tmp_path):
        self._seed(tmp_path, "guide/a.md", "[b](./b.md#目标章节)\n")
        self._seed(tmp_path, "guide/b.md", "## 目标章节\n")
        self._seed_dist(tmp_path, "guide/b.html", ["目标章节"])
        assert self._run(tmp_path) == []

    def test_anchor_miss_is_reported(self, tmp_path):
        # 章节改名后锚点静默失效：vitepress 构建不报错，只有本门禁能拦
        self._seed(tmp_path, "guide/a.md", "[b](./b.md#旧章节)\n")
        self._seed(tmp_path, "guide/b.md", "## 新章节\n")
        self._seed_dist(tmp_path, "guide/b.html", ["新章节"])
        violations = self._run(tmp_path)
        assert len(violations) == 1
        assert "锚点不存在" in violations[0]

    def test_same_page_anchor_is_checked(self, tmp_path):
        self._seed(tmp_path, "guide/a.md", "[x](#缺失锚点)\n")
        self._seed_dist(tmp_path, "guide/a.html", ["存在的锚点"])
        violations = self._run(tmp_path)
        assert len(violations) == 1
        assert "锚点不存在" in violations[0]

    def test_missing_dist_skips_anchor_check(self, tmp_path):
        self._seed(tmp_path, "guide/a.md", "[b](./b.md#任意锚点)\n")
        self._seed(tmp_path, "guide/b.md", "## x\n")
        assert self._run(tmp_path) == []

    def test_root_relative_link_resolves_against_docs_root(self, tmp_path):
        self._seed(tmp_path, "guide/a.md", "[i](/guide/index.md#h)\n")
        self._seed(tmp_path, "guide/index.md", "## h\n")
        self._seed_dist(tmp_path, "guide/index.html", ["h"])
        assert self._run(tmp_path) == []

    def test_missing_image_asset_is_reported(self, tmp_path):
        self._seed(tmp_path, "guide/a.md", "![图](img_1.png)\n")
        violations = self._run(tmp_path)
        assert len(violations) == 1
        assert "img_1.png" in violations[0]
        (tmp_path / "guide" / "img_1.png").write_bytes(b"x")
        assert self._run(tmp_path) == []

    def test_root_relative_asset_falls_back_to_public(self, tmp_path):
        self._seed(tmp_path, "guide/a.md", "![图](/logo.png)\n")
        (tmp_path / "public").mkdir()
        (tmp_path / "public" / "logo.png").write_bytes(b"x")
        assert self._run(tmp_path) == []

    def test_server_path_reference_is_checked(self, tmp_path):
        server = tmp_path / "server_root"
        (server / "ops").mkdir(parents=True)
        (server / "ops" / "legacy_export.py").write_text("", encoding="utf-8")
        self._seed(tmp_path, "guide/a.md", "见 `ops/legacy_export.py` 与 `ops/gone.py`。\n")
        violations = check_docs_site_links.collect_violations(
            docs_root=tmp_path,
            dist_root=tmp_path / "dist",
            server_root=server,
            installer_root=tmp_path / "installer_none",
        )
        assert len(violations) == 1
        assert "ops/gone.py" in violations[0]

    def test_installer_script_reference_resolves_in_installer_repo(self, tmp_path):
        # scripts/ 前缀两个仓都有同名目录：安装器脚本只在安装器仓存在时也应放过
        installer = tmp_path / "installer_root"
        (installer / "scripts").mkdir(parents=True)
        (installer / "scripts" / "7_upgrade.sh").write_text("", encoding="utf-8")
        self._seed(tmp_path, "guide/a.md", "见 `scripts/7_upgrade.sh`。\n")
        assert self._run(tmp_path, installer_root=installer) == []

    def test_missing_installer_repo_skips_that_prefix(self, tmp_path):
        self._seed(tmp_path, "guide/a.md", "见 `scripts/7_upgrade.sh` 的说明。\n")
        assert self._run(tmp_path) == []

    def test_fenced_code_block_is_ignored(self, tmp_path):
        # 围栏内的反引号与伪链接不渲染成链接：不参与校验
        self._seed(tmp_path, "guide/a.md", "```bash\n# 见 `ops/not-a-ref.py` 与 [b](./no-such.md)\n```\n")
        assert self._run(tmp_path) == []

    def test_missing_docs_root_is_skipped(self, tmp_path):
        assert check_docs_site_links.collect_violations(docs_root=tmp_path / "none") == []

    def test_main_require_dist_fails_without_build(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(check_docs_site_links, "_docs_root", lambda: tmp_path)
        (tmp_path / "guide").mkdir()
        original = check_docs_site_links.sys.argv
        check_docs_site_links.sys.argv = ["check_docs_site_links.py", "--require-dist"]
        try:
            assert check_docs_site_links.main() == 1
        finally:
            check_docs_site_links.sys.argv = original
        assert "构建产物缺失" in capsys.readouterr().out


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
