#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 EVENT_CATALOG 生成订阅方事件契约文档（docs/open-platform/events.md）。

用法（容器内或本地 venv，需可 import Django 设置）：
    python scripts/gen_event_docs.py          # 重新生成
    python scripts/gen_event_docs.py --check  # 只校验（本地/发布前校验，漂移即退出 1）

一致性守卫：CI 由守护测试 tests/unit/system/test_webhook_contract.py 保证（漂移即失败），
本脚本的 --check 供发布前手工复核（CI 环境无 config.yml，server.settings 依赖真实配置，
故不在 CI 直接执行本脚本）。

label/description 用「禁用翻译」的原文生成，保证跨环境（有无 .mo）结果可复现。
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

import django  # noqa: E402

django.setup()

from django.utils.translation import override  # noqa: E402

from system.utils.webhook import EVENT_CATALOG  # noqa: E402

DOC_PATH = BASE_DIR / "docs" / "open-platform" / "events.md"


def render() -> str:
    """渲染事件契约文档（唯一真源：EVENT_CATALOG）。"""
    lines = [
        "# 出站 Webhook 事件契约",
        "",
        "> 本文档由 `scripts/gen_event_docs.py` 从 `system/utils/webhook.py` 的 `EVENT_CATALOG` 自动生成，",
        "> 请勿手工编辑；一致性由守护测试 `tests/unit/system/test_webhook_contract.py` 在 CI 保证，",
        "> 本地/发布前可用 `python scripts/gen_event_docs.py --check` 复核。",
        "",
        "## payload 外壳",
        "",
        "所有事件使用统一外壳（`data` 为事件数据，字段契约见下表）：",
        "",
        "```json",
        '{ "event": "<事件 key>", "schema_version": 1, "occurred_at": "2026-09-15T10:30:00+08:00", "data": { } }',
        "```",
        "",
        '- 签名头：`X-Xadmin-Signature`（`sha256=HMAC(secret, "{timestamp}.{raw_body}")`）、',
        "  `X-Xadmin-Timestamp`、`X-Xadmin-Event`、`X-Xadmin-Delivery`（投递主键，幂等键）；",
        "- 投递重试：最多 5 次，退避 `min(60 × 2^(attempt-1), 3600)` 秒；耗尽后订阅行记录",
        "  `last_failure` 并站内信告警，可在「投递审计」页手动重试；",
        "- 版本策略：事件 key 不带版本后缀；破坏性变更新增 `xxx.v2` 事件（旧 key 至少保留一个发布窗口），",
        "  `schema_version` 随契约表递增；新增可选字段不改版本。",
        "",
        "## 事件列表",
        "",
    ]
    for key, entry in EVENT_CATALOG.items():
        lines.append(f"### `{key}`")
        lines.append("")
        lines.append(f"- 版本：{entry.get('version', 1)}")
        lines.append(f"- 说明：{entry.get('label') or key}")
        lines.append("")
        lines.append("| 字段 | 类型 | 必填 | 说明 |")
        lines.append("|---|---|---|---|")
        for name, spec in (entry.get("fields") or {}).items():
            required = "是" if spec.get("required") else "否"
            lines.append(f"| `{name}` | {spec.get('type', 'string')} | {required} | {spec.get('description') or ''} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    check = "--check" in sys.argv
    with override(None):  # 禁用翻译：文档用契约原文，跨环境可复现
        content = render()
    if check:
        current = DOC_PATH.read_text(encoding="utf-8") if DOC_PATH.exists() else ""
        if current != content:
            print("[events] 文档与 EVENT_CATALOG 不一致，请运行 python scripts/gen_event_docs.py 重新生成")
            return 1
        print("[events] 文档与 EVENT_CATALOG 一致")
        return 0
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(content, encoding="utf-8")
    print(f"[events] 已生成 {DOC_PATH.relative_to(BASE_DIR)}（{len(EVENT_CATALOG)} 个事件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
