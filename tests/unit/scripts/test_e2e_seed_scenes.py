# -*- coding: utf-8 -*-
"""E2E 场景种子的产物发现逻辑（`_generator_seed_files`）守护。

背景：代码生成器的菜单种子从「按模型拆分」改为规范文件名（`menu.json` /
`menumeta.json`）后，场景种子仍按 `*_book.json` 通配 → 匹配为空后静默跳过，
二开样板页整页空白（E2E demo-book 与表单标签宽度用例双挂）。这里锁定两种布局
都能被发现，且顺序恒为「menumeta 先于 menu」（菜单 OneToOne 引用 meta）。
"""

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"


def _load_scenes():
    spec = importlib.util.spec_from_file_location("e2e_seed_scenes", SCRIPTS / "e2e_seed_scenes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_manifest_layout_is_authoritative(tmp_path):
    """规范布局：按清单顺序返回，menumeta 先于 menu。"""

    seed_dir = tmp_path / "demo" / "seeds"
    _write(seed_dir / "menu.json", [])
    _write(seed_dir / "menumeta.json", [])
    _write(
        seed_dir / "manifest.json",
        {
            "version": 1,
            "app": "demo",
            "files": [
                {"name": "menu.json", "model": "system.menu", "kind": "contract", "order": 20},
                {"name": "menumeta.json", "model": "system.menumeta", "kind": "contract", "order": 10},
                {"name": "modellabelfield.json", "model": "system.modellabelfield", "kind": "generated", "order": 60},
            ],
        },
    )

    files = _load_scenes()._generator_seed_files(tmp_path)

    assert [path.name for path in files] == ["menumeta.json", "menu.json"]


def test_legacy_split_layout_is_still_discovered(tmp_path):
    """旧布局（无清单、按模型拆分）继续可用：菜单种子不因命名变更而静默丢失。"""

    seed_dir = tmp_path / "demo" / "seeds"
    _write(seed_dir / "menu_book.json", [])
    _write(seed_dir / "menumeta_book.json", [])

    files = _load_scenes()._generator_seed_files(tmp_path)

    assert [path.name for path in files] == ["menumeta_book.json", "menu_book.json"]


def test_empty_output_returns_no_files(tmp_path):
    """生成器什么都没产出时返回空列表（调用方据此打印跳过原因）。"""

    assert _load_scenes()._generator_seed_files(tmp_path) == []
