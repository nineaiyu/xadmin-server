#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : init_data
# author : ly_13
# date : 12/23/2023
"""初始化 / 数据补全脚本（幂等，可重复执行）。

行为：
- 建表（migrate）→ 编译语言包 → 下载 IP 库 → 创建超管（仅首次）→ 导入内置种子；
- 已有环境重复执行时：跳过超管创建，其余动作按幂等语义补全（升级后建议执行一次）；
- 超管初始密码：--admin-password > 环境变量 XADMIN_ADMIN_PASSWORD > 随机生成（仅打印一次）。

用法：
    python utils/init_data.py [--with-demo] [--admin-password <pwd>] [--skip-ip-db]
"""

import argparse
import os
import secrets
import sys

ADMIN_PASSWORD_ENV = "XADMIN_ADMIN_PASSWORD"


def resolve_admin_password(cli_password: str = "") -> str:
    """超管初始密码：命令行 > 环境变量 XADMIN_ADMIN_PASSWORD > 随机生成。

    随机密码仅在初始化输出中打印一次，首次登录后应立即修改；
    生产部署建议始终通过环境变量显式注入。
    """
    password = (cli_password or "").strip()
    if password:
        return password
    password = os.environ.get(ADMIN_PASSWORD_ENV, "").strip()
    if password:
        return password
    return secrets.token_urlsafe(16)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="xadmin-server 初始化 / 数据补全（幂等，可重复执行）")
    parser.add_argument("--with-demo", action="store_true", help="初始化后追加演示数据（seed_demo_all）")
    parser.add_argument("--admin-password", default="", help=f"超管初始密码（等价于环境变量 {ADMIN_PASSWORD_ENV}）")
    parser.add_argument("--skip-ip-db", action="store_true", help="跳过 IP 库下载（离线 / 内网环境）")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

    import django

    django.setup()

    from django.core import management

    from system.models import UserInfo

    # 初始化操作（migrate / compilemessages / download_ip_db 均可重复执行）
    try:
        management.call_command("makemigrations")
        management.call_command("migrate")
        management.call_command("compilemessages")
        if not args.skip_ip_db:
            management.call_command("download_ip_db")
    except Exception as e:
        print(f"[init] migrate/compilemessages failed, continue: {e}")

    # 超管仅首次创建（幂等保护）；已有用户时跳过，继续补全种子数据
    if UserInfo.objects.exists():
        print("[init] user already exists, skip superuser creation")
    else:
        admin_password = resolve_admin_password(args.admin_password)
        UserInfo.objects.create_superuser("xadmin", "xadmin@dvcloud.xin", admin_password)
        if args.admin_password or os.environ.get(ADMIN_PASSWORD_ENV):
            print("[init] superuser xadmin created (password from explicit configuration)")
        else:
            print(
                f"[init] superuser xadmin created, random password (shown once, "
                f"change it after first login): {admin_password}"
            )

    management.call_command("load_init_json")

    if args.with_demo:
        management.call_command("seed_demo_all")

    print("[init] done. Health check: /api/common/api/health")


if __name__ == "__main__":
    main()
