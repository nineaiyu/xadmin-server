# -*- coding: utf-8 -*-
"""`manage.py module remove`（物理移除计划 / 种子清理）单元测试。"""

import json
import os
import shutil
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from common.core.modules import _MODULE_INDEX, ModuleSeedFilter


def run_module_remove(*args):
    out = StringIO()
    call_command("module", *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def scratch_project(tmp_path, settings):
    """把 loadjson 复制到临时项目目录，避免测试触碰仓库种子。"""

    file_root = os.path.join(str(tmp_path), "loadjson")
    os.makedirs(file_root)
    source_root = os.path.join(settings.PROJECT_DIR, "loadjson")
    for name in os.listdir(source_root):
        shutil.copy2(os.path.join(source_root, name), os.path.join(file_root, name))
    settings.PROJECT_DIR = str(tmp_path)
    return file_root


def _load(path):
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


class TestModuleRemovePlan:
    def test_prints_plan_without_touching_files(self, scratch_project):
        before = _load(os.path.join(scratch_project, "menu.json"))
        output = run_module_remove("remove", "chat")
        after = _load(os.path.join(scratch_project, "menu.json"))

        assert before == after, "预演不得修改种子文件"
        assert "物理移除模块：chat" in output
        assert "loadjson/menu.json" in output
        assert "--apply" in output
        assert "未加 --apply" in output

    def test_plan_reports_seed_row_reduction(self, scratch_project):
        output = run_module_remove("remove", "chat")
        # 聊天室：1 个菜单 + 6 个权限点 + 1 个目录修剪，种子行数必然下降
        assert "行 →" in output

    def test_unknown_module_rejected(self, scratch_project):
        with pytest.raises(CommandError, match="未知模块"):
            run_module_remove("remove", "not-exist")

    def test_core_module_rejected(self, scratch_project):
        with pytest.raises(CommandError, match="内核模块不可移除"):
            run_module_remove("remove", "core_rbac")


class TestModuleRemoveFrontendScan:
    """前端待删文件与词条提示（用真实仓库路径，只读预演）。"""

    def test_lists_frontend_files_and_locale_keys(self):
        output = run_module_remove("remove", "chat")
        assert "src/views/chat/index.vue" in output
        assert "src/api/chat/index.ts" in output
        assert "menus.chat" in output

    def test_backend_references_exclude_virtualenv(self):
        output = run_module_remove("remove", "chat")
        assert ".venv" not in output
        # 关键接线点必须出现在清单里（人工据此删除）
        assert "server/urls.py" in output
        assert "message/urls.py" in output


class TestModuleRemoveApply:
    def test_apply_rewrites_seed_and_archives_original(self, scratch_project):
        menu_path = os.path.join(scratch_project, "menu.json")
        meta_path = os.path.join(scratch_project, "menumeta.json")
        original_menus = _load(menu_path)
        original_metas = _load(meta_path)

        output = run_module_remove("remove", "chat", "--apply")

        filtered_menus = _load(menu_path)
        filtered_metas = _load(meta_path)
        assert len(filtered_menus) < len(original_menus)
        # 被剔除的菜单不影响其他模块
        names = {row["fields"]["name"] for row in filtered_menus}
        assert "Chat" not in names
        assert "SystemUser" in names
        # 只被剔除菜单引用的 meta 一并剔除
        assert len(filtered_metas) < len(original_metas)

        # 原文件归档到工作区回收站（可整体还原）：PROJECT_DIR 的上一级 / _delete
        archive_root = os.path.join(os.path.dirname(os.path.dirname(scratch_project.rstrip("/"))), "_delete")
        archives = [name for name in os.listdir(archive_root) if name.startswith("module-chat-")]
        assert archives, "缺少归档目录"
        archived = os.path.join(archive_root, archives[0], "menu.json")
        assert _load(archived) == original_menus
        assert "已改写" in output

    def test_apply_skips_unchanged_files(self, scratch_project):
        """没有变化的种子文件不应被改写（避免无意义 diff）。"""

        untouched = os.path.join(scratch_project, "datadict.json")
        before = os.path.getmtime(untouched)
        run_module_remove("remove", "chat", "--apply")
        assert os.path.getmtime(untouched) == before

    def test_apply_keeps_filter_consistent_with_runtime(self, scratch_project):
        """种子侧剔除口径 == 运行期隐藏口径（同一纯函数）。"""

        run_module_remove("remove", "chat", "--apply")
        menus = _load(os.path.join(scratch_project, "menu.json"))
        spec = _MODULE_INDEX["chat"]
        seed_filter = ModuleSeedFilter([spec])
        hidden = seed_filter.compute_hidden(menus)
        assert "Chat" not in {row["fields"]["name"] for row in menus}
        assert hidden == frozenset(), "剔除后的种子不应再含待隐藏菜单"
