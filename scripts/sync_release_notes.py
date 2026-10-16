#!/usr/bin/env python3
"""把 tag 区间的完整提交变更同步进 GitHub Release 说明。

背景：发布说明由 release-drafter 生成，它**只汇总 Pull Request**——直接推送到
主干的提交（本项目的主要提交方式）不会出现在 Release 中，导致「提交几百条、
Release 只有几条」。发布流水线在 Release 创建后调用本脚本，用按提交类型分组的
完整列表替换「版本变化」段，保留模板的其余段落（升级提示等）。

用法（在仓库根、已 checkout 出完整 tag 历史的环境执行）：
    python3 scripts/sync_release_notes.py <prev_tag> <tag>

依赖：`git`（读取区间提交）、`gh`（读写 Release，需 GITHUB_TOKEN 或已登录）。
"""

from __future__ import annotations

import collections
import re
import subprocess
import sys
import tempfile
from pathlib import Path

#: 分组顺序与标题（未列出的 type 归入「其他」）
TYPE_LABELS: dict[str, str] = collections.OrderedDict(
    [
        ("feat", "新功能"),
        ("fix", "缺陷修复"),
        ("perf", "性能优化"),
        ("refactor", "重构"),
        ("docs", "文档"),
        ("test", "测试"),
        ("chore", "构建与杂项"),
        ("ci", "持续集成"),
    ]
)

#: conventional commit 头部：type(scope)!: subject
COMMIT_HEADER = re.compile(r"^([a-zA-Z]+)(\([^)]*\))?!?:\s*(.*)$")

#: 需要被替换的段落标题（release-config.yml 模板中的「版本变化」）
CHANGES_HEADING = "## 版本变化"


def _run(args: list[str]) -> str:
    """执行命令并返回 stdout（失败抛 CalledProcessError）。"""
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def build_changes_section(prev_tag: str, tag: str) -> str:
    """按提交类型分组生成「版本变化」段（全部提交，折叠呈现）。"""
    raw = _run(["git", "log", "--no-merges", "--format=%h\t%s", f"{prev_tag}..{tag}"])
    buckets: dict[str, list[str]] = {key: [] for key in TYPE_LABELS}
    buckets["其他"] = []
    total = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        sha, subject = line.split("\t", 1)
        total += 1
        matched = COMMIT_HEADER.match(subject)
        key = matched.group(1).lower() if matched else "其他"
        if key not in buckets:
            key = "其他"
        text = (matched.group(3) if matched else subject).strip()
        scope = (matched.group(2) or "").strip("()") if matched else ""
        buckets[key].append(f"- `{sha}` **{scope}** {text}" if scope else f"- `{sha}` {text}")

    lines = [
        CHANGES_HEADING + "  What's Changed",
        "",
        f"> 本版包含 {prev_tag} → {tag} 区间内 **{total} 个提交**（按提交类型分组；"
        "release-drafter 仅汇总 PR，直接推送的提交由本段补齐）。",
        "",
    ]
    for key, label in list(TYPE_LABELS.items()) + [("其他", "其他")]:
        items = buckets[key]
        if not items:
            continue
        lines += [f"<details><summary><b>{label} {key}（{len(items)}）</b></summary>", "", *items, "", "</details>", ""]
    lines.append(f"**完整变更日志**：https://github.com/{_repo_slug()}/compare/{prev_tag}...{tag}")
    return "\n".join(lines)


def _repo_slug() -> str:
    """从 origin 远程地址解析 owner/repo（失败时回退占位）。"""
    try:
        url = _run(["git", "config", "--get", "remote.origin.url"]).strip()
    except subprocess.CalledProcessError:
        return "nineaiyu/xadmin-server"
    matched = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return matched.group(1) if matched else "nineaiyu/xadmin-server"


def replace_changes_section(body: str, changes: str) -> str:
    """用新生成的变更段替换 body 中「版本变化」至下一个二级标题之间的内容。"""
    start = body.find(CHANGES_HEADING)
    if start == -1:
        return f"{changes}\n\n{body}".strip() + "\n"
    rest = body[start + len(CHANGES_HEADING) :]
    matched = re.search(r"\n## ", rest)
    tail = rest[matched.start() + 1 :] if matched else ""
    return f"{body[:start]}{changes}\n\n{tail}".strip() + "\n"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    prev_tag, tag = sys.argv[1], sys.argv[2]

    changes = build_changes_section(prev_tag, tag)
    body = _run(["gh", "release", "view", tag, "--json", "body", "-q", ".body"])
    merged = replace_changes_section(body, changes)

    with tempfile.TemporaryDirectory() as tmp_dir:
        notes_path = Path(tmp_dir) / "notes.md"
        notes_path.write_text(merged, encoding="utf-8")
        subprocess.run(["gh", "release", "edit", tag, "--notes-file", str(notes_path)], check=True)
    print(f"release notes synced for {tag} ({prev_tag}..{tag})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
