# -*- coding: utf-8 -*-
"""文档事实校验脚本守护测试。

1. 当前仓库的受保护事实全部与代码一致（scripts/check_doc_facts.py 在本地 pytest 同样可见，
   不只依赖 CI wiring）；
2. 校验逻辑本身：不一致可报出、事实串被删可报出（防校验器变成静默 no-op）。
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_spec = importlib.util.spec_from_file_location("check_doc_facts", REPO_ROOT / "scripts" / "check_doc_facts.py")
check_doc_facts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_doc_facts)


class TestRepoDocs:
    def test_current_repo_docs_match_code_facts(self):
        assert check_doc_facts.collect_violations(REPO_ROOT) == []


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
