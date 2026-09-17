#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：落盘与共享文件幂等合并。"""

import ast
import sys

from django.core.management.base import CommandError

from .constants import BLOCK_END, BLOCK_START, FIRST_PARTY_TOP_LEVEL, _import_name_sort_key


class MergeMixin:
    """生成产物写入：新建 / 生成块合并 / urls 注册行插入。"""

    @staticmethod
    def _existing_text(path):
        return path.read_text(encoding="utf-8") if path and path.exists() else ""

    @staticmethod
    def _strip_block(text, key):
        """去掉目标文件中本 key 的旧生成块：块内 import 不算「文件已导入」，--force 替换后不丢 import。"""
        if not text:
            return text
        start = BLOCK_START.format(key=key)
        end = BLOCK_END.format(key=key)
        if start not in text or end not in text:
            return text
        head, _, rest = text.partition(start)
        _, _, tail = rest.partition(end)
        return head.rstrip("\n") + tail

    # ------------------------------------------------------------------ 落盘

    def _emit(self, artifacts, options):
        dry_run = options["dry_run"]
        lines = []
        for item in artifacts:
            if item["mode"] == "notice":
                lines.append(f"[提示] {item['label']}：{item['notice']}")
                if dry_run:
                    lines.append(item["content"])
                continue
            if dry_run:
                lines.append(f"----- {item['label']} → {item['path']} (dry-run) -----")
                lines.append(item["content"])
                continue
            item["path"].parent.mkdir(parents=True, exist_ok=True)
            action = self._write(item, force=options["force"])
            lines.append(f"[{action}] {item['label']} → {item['path']}")
        lines.append(
            "\n提示：生成后请复核两点——关联字段 input_type 是否符合数据量；"
            "菜单是否需要挂到已有目录（--parent 或菜单管理里改上级）。"
        )
        self.stdout.write("\n".join(lines))

    def _write(self, item, force):
        path = item["path"]
        if item["mode"] == "create":
            if path.exists() and not force:
                return "跳过（已存在，--force 覆盖）"
            path.write_text(item["content"], encoding="utf-8")
            return "写入"
        if item["mode"] == "block":
            return self._merge_block(path, item["key"], item["content"], force)
        if item["mode"] == "urls":
            if not path.exists():
                path.write_text(item["content"], encoding="utf-8")
                return "写入"
            return self._merge_urls(path, item)
        raise CommandError(f"未知产物模式：{item['mode']}")

    def _merge_block(self, path, key, content, force):
        """共享文件（views.py / serializers.py）的生成块合并：按标记整块替换，幂等。"""
        start = BLOCK_START.format(key=key)
        end = BLOCK_END.format(key=key)
        # 模块级类之后紧跟注释需两个空行（ruff format 口径），故块尾补足空行
        body = content.rstrip("\n")
        block = f"{start}\n{body}\n\n\n{end}\n"
        if not path.exists():
            # 新建的共享文件同样带标记：后续执行据此识别生成块（幂等前提）
            path.write_text(block, encoding="utf-8")
            return "写入"
        text = path.read_text(encoding="utf-8")
        if start in text and end in text:
            if not force:
                return "跳过（生成块已存在，--force 替换）"
            head, _, rest = text.partition(start)
            _, _, tail = rest.partition(end)
            path.write_text(head + block + tail.lstrip("\n"), encoding="utf-8")
            return "更新生成块"
        # 文件尾部若为模块级定义，标记前需两个空行（ruff format 口径）
        path.write_text(text.rstrip("\n") + "\n\n\n" + block, encoding="utf-8")
        return "追加生成块"

    def _merge_urls(self, path, item):
        """urls.py：顶部补 import、urlpatterns 前插入 router.register 注册行（幂等）。"""
        ctx = item["ctx"]
        text = path.read_text(encoding="utf-8")
        register = f'router.register("{ctx["router_path"]}", {ctx["model_name"]}ViewSet, basename="{ctx["basename"]}")'
        if register in text:
            return "跳过（注册行已存在）"
        if "SimpleRouter" not in text:
            raise CommandError(f"{path} 非 SimpleRouter 形态，请手工注册：{register}")
        import_line = f"from {ctx['view_module']} import {ctx['model_name']}ViewSet"
        lines = text.splitlines()
        if import_line not in lines:
            lines = self._merge_urls_import(lines, ctx, import_line)
        insert_at = next((index for index, line in enumerate(lines) if line.startswith("urlpatterns")), len(lines))
        lines.insert(insert_at, register)
        lines.insert(insert_at + 1, "")
        path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
        return "插入注册行"

    @classmethod
    def _merge_urls_import(cls, lines, ctx, import_line):
        """把 ViewSet import 合入第一方 import 块并整体重排（防 I001）。

        复用 `_group_imports`：同模块 from-import 合并、组内按模块排序、组间空行；
        目标块优先取已有第一方 import 的连续块，否则取最后一个 import 块。
        直 `import x` 行保持块首（isort 口径：同 section 内直 import 在 from-import 前）。
        """
        import_indexes = [index for index, line in enumerate(lines) if line.startswith(("import ", "from "))]
        if not import_indexes:
            return [import_line, *lines]
        blocks: list[list[int]] = []
        for index in import_indexes:
            if blocks and index == blocks[-1][-1] + 1:
                blocks[-1].append(index)
            else:
                blocks.append([index])

        def is_first_party(line):
            return line.startswith("from ") and line[len("from ") :].split()[0].split(".")[0] in FIRST_PARTY_TOP_LEVEL

        target = next((block for block in blocks if any(is_first_party(lines[index]) for index in block)), blocks[-1])
        head, tail = target[0], target[-1]
        block_lines = [*lines[head : tail + 1], import_line]
        plain = [line for line in block_lines if not line.startswith("from ")]
        froms = [line for line in block_lines if line.startswith("from ")]
        return [*lines[:head], *plain, *cls._group_imports(froms), *lines[tail + 1 :]]

    # ------------------------------------------------------------ 导入去重工具

    @staticmethod
    def _imported_names(text):
        """目标文件顶层已导入的名字集合（含别名），用于生成块内 import 去重（防 F811）。"""
        names = set()
        if not text:
            return names
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return names
        for node in tree.body:
            if isinstance(node, ast.Import):
                names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.update(alias.asname or alias.name for alias in node.names)
        return names

    def _render_imports(self, specs, existing_names):
        """specs: [(module, [(name, asname), ...])]；已导入的名字跳过，避免重定义。"""
        lines = []
        for module, items in specs:
            missing = [(name, asname) for name, asname in items if (asname or name) not in existing_names]
            if not missing:
                continue
            rendered = ", ".join(f"{name} as {asname}" if asname else name for name, asname in missing)
            lines.append(f"from {module} import {rendered}")
        return lines

    @staticmethod
    def _group_imports(lines):
        """import 行按「标准库 → 第三方 → 第一方」分组，组内按模块排序并合并同模块。

        第一方（common/system/message 与生成的 app）**同属一组、组内不留空行**——
        口径与 ruff.toml 的 [lint.isort].known-first-party 一致（否则生成物 I001）；
        同模块的多条 from-import 合并为一行（isort 默认行为），name 顺序保持调用方给定。
        """
        merged: dict[str, list[str]] = {}
        for line in lines:
            module, names = line[len("from ") :].split(" import", 1)
            bucket = merged.setdefault(module.strip(), [])
            for name in names.split(","):
                name = name.strip()
                if name and name not in bucket:
                    bucket.append(name)

        groups = {"stdlib": [], "third": [], "app": []}
        for module in sorted(merged):
            top = module.split(".")[0]
            if top in sys.stdlib_module_names:
                key = "stdlib"
            elif top in FIRST_PARTY_TOP_LEVEL:
                key = "app"
            else:
                key = "third"
            names = sorted(merged[module], key=_import_name_sort_key)
            groups[key].append(f"from {module} import {', '.join(names)}")

        ordered = []
        for key in ("stdlib", "third", "app"):
            if groups[key]:
                ordered.extend(groups[key])
                ordered.append("")
        return ordered[:-1] if ordered else ordered
