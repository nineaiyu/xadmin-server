#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""凭据轮换与巡检（P-3）：重加密敏感配置 + 明文巡检 + 审计留痕。

用法：
    python manage.py rotate_credential --audit             # 只巡检（发现明文退出码 1）
    python manage.py rotate_credential --all --dry-run     # 预演全部敏感项
    python manage.py rotate_credential --key SCIM_TOKEN --yes
    python manage.py rotate_credential --all --yes         # 全部重加密（含 Setting 加密项）

口径：
- SystemConfig 敏感键：``SENSITIVE_SETTING_KEYS`` 注册表（值内字段级加密）；
  明文 → 首次加密；密文 → 解密后重加密（轮换 salt/nonce）；
- Setting（``encrypted=True``）：重写密文（同样的重加密语义）；
- 落库前默认 dry-run，必须显式 ``--yes`` 才执行（高危动作二次确认）；
- 每次执行写 OperationLog(module=system:credential) 审计，并失效配置缓存。
"""

import sys

from django.core.management.base import BaseCommand
from django.utils import timezone

from common.core.credentials import (
    SENSITIVE_KEY_PATTERN,
    SENSITIVE_SETTING_KEYS,
    encryption_status,
    plaintext_sensitive_keys,
)
from common.utils import get_logger

logger = get_logger(__name__)


def _plaintext_setting_names() -> list:
    """Setting 中「名字敏感但 encrypted=False」的行（明文风险面）。"""
    from settings.models import Setting

    offenders = []
    for name in Setting.objects.filter(encrypted=False).values_list("name", flat=True):
        if SENSITIVE_KEY_PATTERN.search(str(name or "")):
            offenders.append(name)
    return sorted(offenders)


class Command(BaseCommand):
    help = "轮换/巡检敏感凭据（SystemConfig 值内加密 + Setting 加密项重加密）"

    def add_arguments(self, parser):
        parser.add_argument("--key", action="append", default=[], help="指定 SystemConfig 键（可多次）")
        parser.add_argument("--all", action="store_true", help="处理全部敏感项（含 Setting 加密项）")
        parser.add_argument("--audit", action="store_true", help="只巡检明文，不改库")
        parser.add_argument("--dry-run", action="store_true", help="只打印计划，不改库")
        parser.add_argument("--yes", action="store_true", help="确认执行（省略时等同 dry-run）")

    def handle(self, *args, **options):
        keys = [key for key in (options.get("key") or []) if key]
        if options.get("all") and not keys:
            keys = sorted(SENSITIVE_SETTING_KEYS)
        for key in [item for item in keys if item not in SENSITIVE_SETTING_KEYS]:
            self.stdout.write(self.style.WARNING(f"[skip] {key}: 不在敏感键注册表 SENSITIVE_SETTING_KEYS"))
        keys = [key for key in keys if key in SENSITIVE_SETTING_KEYS]

        if options.get("audit"):
            return self._audit_only(keys)

        from system.models import SystemConfig
        from system.utils.credential import rotate_setting, rotate_system_config

        execute = bool(options.get("yes")) and not options.get("dry_run")
        rotated = skipped = 0
        for key in keys:
            if not execute:
                row = SystemConfig.objects.filter(key=key).first()
                status = encryption_status(key, row.value if row else None)
                if status in ("empty", "not_sensitive"):
                    self.stdout.write(f"[skip] {key}: 未配置（空值无需加密）")
                    skipped += 1
                    continue
                action = "encrypt" if status == "plaintext" else "rotate"
                self.stdout.write(f"[计划 {action}] {key}")
                rotated += 1
                continue
            result = rotate_system_config(key)
            if result.get("action") == "skip":
                self.stdout.write(f"[skip] {key}: {result.get('detail')}")
                skipped += 1
            elif result.get("ok"):
                self.stdout.write(self.style.SUCCESS(f"[{result['action']}] {key}"))
                rotated += 1
            else:
                self.stdout.write(self.style.ERROR(f"[fail] {key}: {result.get('detail')}"))
                skipped += 1

        setting_count = 0
        if options.get("all"):
            from settings.models import Setting

            names = list(Setting.objects.filter(encrypted=True).exclude(value="").values_list("name", flat=True))
            for name in names:
                if not execute:
                    self.stdout.write(f"[计划 rotate] Setting {name}")
                    setting_count += 1
                    continue
                result = rotate_setting(name)
                if result.get("ok"):
                    self.stdout.write(self.style.SUCCESS(f"[rotate] Setting {name}"))
                    setting_count += 1
                else:
                    self.stdout.write(self.style.WARNING(f"[skip] Setting {name}: {result.get('detail')}"))

        mode = "执行" if execute else "预演（加 --yes 才落库）"
        self.stdout.write(
            self.style.SUCCESS(f"{mode}完成：SystemConfig {rotated} 项 / Setting {setting_count} 项，跳过 {skipped} 项")
        )
        return None

    def _audit_only(self, keys):
        """只巡检：明文敏感项输出清单，发现即非零退出（可入运维巡检/CI）。"""
        from system.models import SystemConfig

        offenders = plaintext_sensitive_keys()
        if keys:
            offenders = [key for key in offenders if key in set(keys)]
        for key in offenders:
            row = SystemConfig.objects.filter(key=key).first()
            self.stdout.write(self.style.ERROR(f"[plaintext] {key}: {str(getattr(row, 'value', ''))[:40]}…"))
        setting_offenders = _plaintext_setting_names()
        for name in setting_offenders:
            self.stdout.write(self.style.ERROR(f"[plaintext] Setting {name}: encrypted=False"))
        if offenders or setting_offenders:
            self.stdout.write(self.style.ERROR(f"发现 {len(offenders) + len(setting_offenders)} 项明文敏感凭据"))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS(f"巡检通过（{timezone.now().isoformat()}）：未发现明文敏感凭据"))
        return None
