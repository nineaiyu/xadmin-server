#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 message/ws_schema.py 生成 WebSocket 帧协议 Schema（docs/schema/ws-frame.schema.json）。

用法（本地 venv，无需 config.yml / Django 初始化——真源模块为纯 Python）：
    python scripts/gen_ws_frame_schema.py          # 重新生成
    python scripts/gen_ws_frame_schema.py --check  # 只校验（漂移即退出 1）

一致性守卫：CI 由守护测试 tests/unit/message/test_ws_frame_schema.py 保证
（重算与落盘文件逐字节比对，手改 schema 即失败）；本脚本供变更后重新生成。
client 仓经 `pnpm sync:contract` 同步镜像并重新生成 TS 类型。
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from message.ws_schema import render  # noqa: E402

SCHEMA_PATH = BASE_DIR / "docs" / "schema" / "ws-frame.schema.json"


def main() -> int:
    check = "--check" in sys.argv
    content = render()
    if check:
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
        if current != content:
            print("[ws-frame] schema 与真源不一致，请运行 python scripts/gen_ws_frame_schema.py 重新生成")
            return 1
        print("[ws-frame] schema 与真源一致")
        return 0
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(content, encoding="utf-8")
    print(f"[ws-frame] 已生成 {SCHEMA_PATH.relative_to(BASE_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
