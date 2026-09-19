#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : module
# author : ly_13
# date : 2026/09/17
"""功能模块硬裁剪（物理移除前的计划与种子清理）。

软裁剪（config.yml 关模块）保留代码、只隐藏入口；本命令面向"确定不再需要某模块"的
二开场景，给出**可核对、可回滚**的物理移除路径：

    python manage.py module remove chat              # 只输出执行计划（不动任何文件）
    python manage.py module remove chat --apply       # 执行「种子裁剪」：原文件先归档再改写

`--apply` 只做一件事：把该模块的菜单/权限点/字段权限/角色菜单绑定从 `loadjson/` 里剔除
（原文件先移动到工作区回收站 `_delete/module-<id>-<时间戳>/`，可整体还原）。代码目录、
路由与 INSTALLED_APPS 的删除由人工按计划清单执行——命令不做源码改写。

裁剪语义见 docs/architecture/模块化与功能裁剪.md。
"""

import json
import os
import shutil
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from common.core.modules import CORE, ModuleSeedFilter, config_snippet, module_index

# loadjson 中可能引用菜单主键的文件（按过滤器实际产出比较，只有变化的文件才会被改写）。
# menu 必须排在 menumeta 之前：meta 过滤依赖菜单侧记录的引用关系
SEED_MODEL_NAMES = (
    "menu",
    "menumeta",
    "fieldpermission",
    "userrole",
    "datapermission",
)
# 归档目录名（工作区级回收站，不属于任何子仓库）
ARCHIVE_ROOT_NAME = "_delete"


class Command(BaseCommand):
    # argparse 的 help 必须是 str（惰性翻译对象会抛 TypeError，详见 modules.py 注释）
    help = "Plan (and optionally apply) the physical removal of a functional module"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["remove"], help="目前仅支持 remove")
        parser.add_argument("module_id", help="模块 id，见 python manage.py modules")
        parser.add_argument("--apply", action="store_true", help="执行种子裁剪（默认只输出计划）")

    def handle(self, *args, **options):
        module_id = options["module_id"]
        spec = module_index().get(module_id)
        if spec is None:
            raise CommandError(f"未知模块：{module_id}；可用：{', '.join(sorted(module_index()))}")
        if spec.level == CORE:
            raise CommandError(f"内核模块不可移除：{module_id}")

        apply_changes = options["apply"]
        self.stdout.write(f"=== 物理移除模块：{spec.id}（{spec.label}，等级 {spec.level}）===")
        self.stdout.write("")

        # 1) 先软裁剪：让运行期影响面可评审（不删数据、可回滚）
        self.stdout.write("【1】先软裁剪并重启，确认业务无依赖：")
        self.stdout.write(f"    MODULE_DISABLE:\n      - {module_id}")
        self.stdout.write("    参考：python manage.py modules --disable " + module_id)
        self.stdout.write("")

        # 2) 种子清理（本命令唯一会写文件的部分）
        seed_plan = self._seed_plan(module_id)
        self.stdout.write("【2】种子清理（新增模块的菜单/权限点不再随 load_init_json 入库）：")
        if not seed_plan:
            self.stdout.write("    无需改动（该模块在种子里没有可剔除的条目）")
        for name, (before, after) in sorted(seed_plan.items()):
            self.stdout.write(f"    loadjson/{name}.json：{before} 行 → {after} 行")
        if apply_changes and seed_plan:
            archive = self._apply_seed(module_id, seed_plan)
            self.stdout.write(f"    ✅ 已改写；原文件归档到：{archive}（可整体还原）")
        elif seed_plan:
            self.stdout.write("    （未加 --apply：以上为预演，未改动任何文件）")
        self.stdout.write("")

        # 3) 代码引用清单（人工核对后删除）
        scan = self._reference_scan(module_id)
        self.stdout.write("【3】后端代码引用（自动扫描，人工确认后删除）：")
        if scan["code"]:
            for line in scan["code"]:
                self.stdout.write(f"    {line}")
        else:
            self.stdout.write("    未扫描到生产代码引用（仍需人工核对前端与种子）")
        if scan["truncated"]:
            self.stdout.write(f"    ... 其余 {scan['truncated']} 处略")
        self.stdout.write(f"    （tests/ 中另有 {scan['tests_count']} 处引用，随对应用例一同删除）")
        self.stdout.write("")
        # 两段各自判空：单仓检出（无前端兄弟仓）时文件清单为空、词条清单仍来自种子，
        # 按内容分别输出（避免打印只有标题的空清单）
        frontend = self._frontend_scan(module_id)
        if frontend["files"]:
            self.stdout.write("【3.1】前端页面文件（组件路径来自菜单种子，建议整目录删除）：")
            for line in frontend["files"]:
                self.stdout.write(f"    {line}")
            if frontend["truncated"]:
                self.stdout.write(f"    ... 其余 {frontend['truncated']} 个文件略")
        if frontend["locale_keys"]:
            self.stdout.write("【3.2】i18n 词条（locales/zh-CN.yaml 与 en.yaml 同步删除）：")
            self.stdout.write("    " + ", ".join(frontend["locale_keys"]))
        if frontend["files"] or frontend["locale_keys"]:
            self.stdout.write("")

        # 4) 门禁
        self.stdout.write("【4】改完跑门禁：")
        self.stdout.write("    pytest -q && ruff check . && ruff format --check .")
        self.stdout.write("    前端：pnpm lint && pnpm typecheck && pnpm test:run && pnpm test:e2e:smoke")
        self.stdout.write("")
        self.stdout.write(f"当前裁剪配置（供对照）：\n{config_snippet()}")

    # ------------------------------------------------------------------ 种子

    def _seed_plan(self, module_id):
        """计算种子文件的变化（不写文件）：{文件名: (原行数, 过滤后行数)}。"""

        spec = module_index()[module_id]
        seed_filter = ModuleSeedFilter([spec])
        file_root = os.path.join(settings.PROJECT_DIR, "loadjson")
        menu_path = os.path.join(file_root, "menu.json")
        if not os.path.exists(menu_path):
            return {}
        with open(menu_path, encoding="utf-8") as fp:
            seed_filter.hidden = seed_filter.compute_hidden(json.load(fp))

        plan = {}
        for model_name in SEED_MODEL_NAMES:
            path = os.path.join(file_root, f"{model_name}.json")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fp:
                rows = json.load(fp)
            label = f"system.{model_name}"
            filtered = seed_filter.filter_rows(label, rows)
            if len(filtered) != len(rows):
                plan[f"{model_name}"] = (len(rows), len(filtered))
        return plan

    def _apply_seed(self, module_id, plan) -> str:
        """执行种子裁剪：先归档原文件，再就地写回过滤结果。"""

        spec = module_index()[module_id]
        seed_filter = ModuleSeedFilter([spec])
        file_root = os.path.join(settings.PROJECT_DIR, "loadjson")
        archive = os.path.join(self._archive_root(), f"module-{module_id}-{time.strftime('%Y%m%d-%H%M%S')}")
        os.makedirs(archive, exist_ok=True)
        with open(os.path.join(file_root, "menu.json"), encoding="utf-8") as fp:
            seed_filter.hidden = seed_filter.compute_hidden(json.load(fp))

        for model_name in plan:
            source = os.path.join(file_root, f"{model_name}.json")
            shutil.copy2(source, os.path.join(archive, f"{model_name}.json"))
            with open(source, encoding="utf-8") as fp:
                rows = json.load(fp)
            filtered = seed_filter.filter_rows(f"system.{model_name}", rows)
            with open(source, "w", encoding="utf-8") as fp:
                json.dump(filtered, fp, ensure_ascii=False, indent=1)
                fp.write("\n")
        return archive

    @staticmethod
    def _archive_root() -> str:
        """工作区回收站：后端仓库的上一级（xadmin 工作区根）下的 _delete/。"""

        return os.path.join(os.path.dirname(settings.PROJECT_DIR), ARCHIVE_ROOT_NAME)

    # ------------------------------------------------------------------ 引用扫描

    def _reference_scan(self, module_id, limit: int = 30) -> dict:
        """扫描后端仓库中引用该模块的位置。

        关键词分两类，避免"Chat"命中 ChatRoom/ChatMessage 之类的噪声：
        - 路径类（路由前缀、权限点前缀）按原文匹配；
        - 标识类（模块 id、菜单 name）按带引号的字面量匹配（如 ``module="chat"``）。

        用例目录（tests/）的命中只计数不逐行列出——它们随用例删除，不构成决策信息。
        """

        spec = module_index()[module_id]
        project_dir = settings.PROJECT_DIR
        path_keywords = set(spec.routes) | set(spec.permissions)
        quoted = {f'"{module_id}"', f"'{module_id}'"}
        quoted.update(f'"{name}"' for name in spec.menus)
        quoted.update(f"'{name}'" for name in spec.menus)

        code_hits, tests_count = [], 0
        for root, dirs, files in os.walk(project_dir):
            # 跳过隐藏目录（.venv / .git / .venv.py313.bak 等备份环境）与大体量目录
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in {"node_modules", "data", "tmp", "loadjson", "docs", "__pycache__"}
            ]
            for filename in files:
                if not filename.endswith(".py") or filename.startswith("."):
                    continue
                path = os.path.join(root, filename)
                rel = os.path.relpath(path, project_dir)
                for lineno, text in enumerate(self._read_lines(path), start=1):
                    if not (any(keyword in text for keyword in path_keywords) or any(k in text for k in quoted)):
                        continue
                    if rel.startswith("tests/"):
                        tests_count += 1
                    else:
                        code_hits.append(f"{rel}:{lineno}: {text.strip()[:110]}")
        return {
            "code": code_hits[:limit],
            "truncated": max(0, len(code_hits) - limit),
            "tests_count": tests_count,
        }

    def _frontend_scan(self, module_id) -> dict:
        """前端待删文件与 i18n 词条（组件路径/词条均来自菜单种子，与前端目录无关）。

        前端组件按「菜单 component 子串」解析（与 src/router/utils.ts 的匹配口径一致），
        因此在 client 仓库里搜含该子串的文件即可列出待删页面与其 api 兄弟文件。

        词条清单只依赖种子（menu/menumeta），单仓检出（无前端兄弟仓）时照常给出；
        文件清单需要前端目录树，缺失时为空（不报错）。
        """

        seed_filter = ModuleSeedFilter([module_index()[module_id]])
        file_root = os.path.join(settings.PROJECT_DIR, "loadjson")
        menu_path = os.path.join(file_root, "menu.json")
        result = {"files": [], "locale_keys": [], "truncated": 0}
        if not os.path.exists(menu_path):
            return result

        with open(menu_path, encoding="utf-8") as fp:
            menu_rows = json.load(fp)
        hidden = seed_filter.compute_hidden(menu_rows)
        metas = {row["pk"]: row["fields"] for row in self._read_json(os.path.join(file_root, "menumeta.json"))}
        components, locale_keys = set(), set()
        for row in menu_rows:
            if row["pk"] not in hidden:
                continue
            fields = row["fields"]
            if fields.get("component"):
                components.add(str(fields["component"]))
            title = str(metas.get(fields.get("meta"), {}).get("title") or "")
            if title and title.isascii() and "." in title and " " not in title:
                locale_keys.add(title)
        result["locale_keys"] = sorted(locale_keys)

        client_src = os.path.join(os.path.dirname(settings.PROJECT_DIR), "xadmin-client", "src")
        if not os.path.isdir(client_src):
            return result

        matched = []
        for root, dirs, files in os.walk(client_src):
            dirs[:] = [d for d in dirs if d not in {"node_modules", "__pycache__"}]
            for filename in files:
                path = os.path.join(root, filename)
                rel = os.path.relpath(path, os.path.dirname(client_src))
                if rel.endswith((".vue", ".ts", ".tsx")) and any(component in rel for component in components):
                    matched.append(rel)
        result["files"] = sorted(matched)[:40]
        result["truncated"] = max(0, len(matched) - 40)
        return result

    @staticmethod
    def _read_json(path) -> list:
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)

    @staticmethod
    def _read_lines(path):
        try:
            with open(path, encoding="utf-8") as fp:
                return fp.readlines()
        except (OSError, UnicodeDecodeError):  # pragma: no cover - 二进制/无权限文件
            return []
