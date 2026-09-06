#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T3.1 性能基线种子数据：批量创建/重置压测用户（username 前缀 perf_）。

用途：
- 为列表/导出/导入压测提供稳定规模的数据集（规模必须固定，基线才可比）；
- k6 06-import.js 的 update 模式从这批用户取主键。

用法（在 xadmin-server 目录下）：
    python loadtest/seed_users.py --count 1000
可选参数：
    --base-url 仅打印提示用；脚本直接操作 ORM，不走 HTTP
    --wipe-only 只清理不创建

注意：脚本会先删除所有 username 以 perf_ 开头的用户，只在压测专用环境执行！
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

import django  # noqa: E402

django.setup()

from django.contrib.auth.hashers import make_password  # noqa: E402

from system.models import UserInfo  # noqa: E402

PERF_PREFIX = "perf_"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1000, help="创建压测用户数量（默认 1000）")
    parser.add_argument("--wipe-only", action="store_true", help="只清理 perf_ 用户，不创建")
    args = parser.parse_args()

    deleted, _ = UserInfo.objects.filter(username__startswith=PERF_PREFIX).delete()
    print(f"已清理旧压测用户 {deleted} 个")

    if args.wipe_only:
        return

    # 压测用户不参与登录，共用一个哈希即可；bulk_create 避免逐个 PBKDF2 拖慢种子过程
    common_hash = make_password("Perf@123456")
    batch = [
        UserInfo(
            username=f"{PERF_PREFIX}{i:05d}",
            password=common_hash,
            nickname=f"压测用户{i:05d}",
            description="k6 baseline seed",
            is_active=True,
        )
        for i in range(args.count)
    ]
    UserInfo.objects.bulk_create(batch, batch_size=500)
    print(f"已创建 {len(batch)} 个压测用户（{PERF_PREFIX}00000 ~ {PERF_PREFIX}{args.count - 1:05d}）")


if __name__ == "__main__":
    main()
