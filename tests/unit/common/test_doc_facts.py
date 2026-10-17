# -*- coding: utf-8 -*-
"""文档事实校验脚本守护测试。

1. 当前仓库的受保护事实全部与代码一致（scripts/check_doc_facts.py 在本地 pytest 同样可见，
   不只依赖 CI wiring）；
2. 校验逻辑本身：不一致可报出、事实串被删可报出（防校验器变成静默 no-op）。
"""

import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_spec = importlib.util.spec_from_file_location("check_doc_facts", REPO_ROOT / "scripts" / "check_doc_facts.py")
check_doc_facts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_doc_facts)


class TestRepoDocs:
    def test_current_repo_docs_match_code_facts(self):
        assert check_doc_facts.collect_violations(REPO_ROOT) == []


class TestAdrCountSource:
    """ADR 篇数事实源（adr:count）：索引写「N 篇」必须等于实际 ADR 文件数。"""

    def test_adr_count_matches_files(self):
        expected = len(list((REPO_ROOT / "docs" / "adr").glob("ADR-*.md")))
        assert check_doc_facts._adr_count() == str(expected)

    def test_index_count_claim_matches_actual(self):
        text = (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        matched = re.search(r"架构决策记录索引（(\d+) 篇", text)
        assert matched, "文档索引里的 ADR 篇数描述被删（受保护事实失去载体）"
        assert matched.group(1) == check_doc_facts._adr_count()


class TestInstallerBaselineSource:
    """升级基线事实源（installer:required_version）：安装器脚本是唯一事实源。

    文档写「版本门槛 vX.Y.Z」必须等于 ``scripts/7_upgrade.sh`` 真正执行的门槛，
    否则用户按文档升级会在第一步被拦下（或误以为可原地升级）。
    """

    BASELINE_FACT = {
        "doc": "guide/upgrade-installer.md",
        "external": True,
        "label": "安装器升级页版本门槛",
        "pattern": r"当前版本需 ≥ `v(\d+\.\d+\.\d+)`",
        "source": "installer:required_version",
    }

    def test_script_constant_is_the_source(self, tmp_path, monkeypatch):
        script = tmp_path / "scripts" / "7_upgrade.sh"
        script.parent.mkdir(parents=True)
        script.write_text('  required_version="4.2.5"\n', encoding="utf-8")
        monkeypatch.setenv("XADMIN_INSTALLER_DIR", str(tmp_path))
        assert check_doc_facts._installer_upgrade_baseline() == "4.2.5"

    def test_missing_installer_repo_is_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XADMIN_INSTALLER_DIR", str(tmp_path / "none"))
        assert check_doc_facts._installer_upgrade_baseline() is None

    def test_stale_claim_is_reported(self, tmp_path, monkeypatch):
        docs = tmp_path / "docs"
        guide = docs / "guide"
        guide.mkdir(parents=True)
        (guide / "upgrade-installer.md").write_text(
            "2. **确认版本门槛**：当前版本需 ≥ `v3.10.11`（更早的库需走\n", encoding="utf-8"
        )
        monkeypatch.setattr(check_doc_facts, "_resolve_source", lambda source: "4.2.5")
        violations = check_doc_facts.collect_violations(
            tmp_path,
            [dict(self.BASELINE_FACT)],
            docs_root=docs,
            client_root=tmp_path / "none",
        )
        assert len(violations) == 1
        assert "3.10.11" in violations[0] and "4.2.5" in violations[0]

    def test_baseline_claim_carriers_exist(self):
        # 三处门槛文案的载体与措辞：正则写死措辞，改版时显式失败而非静默漏检
        facts = [fact for fact in check_doc_facts.FACTS if fact["source"] == "installer:required_version"]
        assert len(facts) == 3
        for fact in facts:
            assert fact["pattern"].startswith(("当前版本需 ≥", "版本门槛为", "要求当前版本 ≥"))


class TestCheckerLogic:
    def test_mismatch_is_reported(self, tmp_path, monkeypatch):
        (tmp_path / "a.md").write_text("当前运行 Django 5.2.9", encoding="utf-8")
        facts = [
            {"doc": "a.md", "label": "Django", "pattern": r"Django (\d+\.\d+\.\d+)", "source": "requirements:django"}
        ]
        monkeypatch.setattr(check_doc_facts, "_resolve_source", lambda source: "6.0.8")
        violations = check_doc_facts.collect_violations(tmp_path, facts)
        assert len(violations) == 1
        assert "5.2.9" in violations[0] and "6.0.8" in violations[0]

    def test_removed_fact_is_reported(self, tmp_path, monkeypatch):
        (tmp_path / "a.md").write_text("这里没有任何版本串", encoding="utf-8")
        facts = [
            {"doc": "a.md", "label": "Django", "pattern": r"Django (\d+\.\d+\.\d+)", "source": "requirements:django"}
        ]
        monkeypatch.setattr(check_doc_facts, "_resolve_source", lambda source: "6.0.8")
        violations = check_doc_facts.collect_violations(tmp_path, facts)
        assert len(violations) == 1
        assert "未匹配到" in violations[0]

    def test_missing_doc_is_reported(self, tmp_path):
        # docs_root / client_root 显式指向不存在目录：跨仓事实走"根缺失跳过"分支，
        # 断言只针对本地受保护事实（避免测试耦合本机跨仓检出状态）
        violations = check_doc_facts.collect_violations(
            tmp_path,
            check_doc_facts.FACTS,
            docs_root=tmp_path / "none",
            client_root=tmp_path / "none",
        )
        assert violations and all("文档不存在" in item for item in violations)
