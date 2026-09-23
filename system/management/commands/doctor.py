#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""环境自检（doctor）：一条命令给出「可启动 + 可扩展」所需的全部检查与修复命令。

检查项（只读，不修改任何数据）：
1. 配置与密钥：SECRET_KEY 来源（显式 / 自动生成）与自动密钥文件权限；
2. 数据库连通；
3. Redis 缓存读写；
4. 语言包编译（locale/*/LC_MESSAGES/*.mo 缺失或过期 → 界面回退英文）；
5. 权限点缺口（代码路由 ↔ 库内权限点，非超管 403 的根因）；
6. 模块裁剪配置（config.yml 的 MODULE_PRESET / MODULE_ENABLE / MODULE_DISABLE 有效性）；
7. 前端契约镜像（contract/schema ↔ docs/schema；单仓检出自动跳过）；
8. 前后端版本一致性（server/const.py ↔ xadmin-client/package.json；单仓检出自动跳过）。

退出码：存在失败项返回 1（可用于 CI）；仅警告返回 0。

用法：
    python manage.py doctor
    python manage.py doctor --skip-permissions    # 跳过权限点扫描（大库提速）
"""

import json
import stat
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

PASS = "✅"
WARN = "⚠️"
FAIL = "❌"
DETAIL_LIMIT = 8
# 探针缓存键：仅本文件使用（缓存键审计门禁按「单文件前缀」判定）
CACHE_PROBE_KEY = "xadmin_doctor_probe"


class Command(BaseCommand):
    help = "Self-check the runtime environment (read-only) and print fix commands"

    def add_arguments(self, parser):
        parser.add_argument("--skip-permissions", action="store_true", help="跳过权限点缺口扫描（大库提速）")

    def handle(self, *args, **options):
        self._passed = 0
        self._warnings = 0
        self._failures = 0

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("[xadmin doctor] 环境自检（只读，不会修改任何数据）"))
        self.stdout.write("-" * 64)

        self._check_secret_key()
        self._check_database()
        self._check_redis()
        self._check_locale()
        if not options["skip_permissions"]:
            self._check_permissions()
        self._check_modules()
        self._check_ai_declarations()
        self._check_frontend_contract()
        self._check_version_sync()

        self.stdout.write("-" * 64)
        summary = f"结果：通过 {self._passed} / 警告 {self._warnings} / 失败 {self._failures}"
        if self._failures:
            self.stdout.write(self.style.ERROR(summary))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(summary))

    # ------------------------------------------------------------------ 输出

    def _report(self, mark, name, detail, fix=None):
        if mark == FAIL:
            self._failures += 1
        elif mark == WARN:
            self._warnings += 1
        else:
            self._passed += 1
        style = {PASS: self.style.SUCCESS, WARN: self.style.WARNING, FAIL: self.style.ERROR}[mark]
        self.stdout.write(style(f"[{mark}] {name}：{detail}"))
        if fix:
            self.stdout.write(f"       ↳ 修复：{fix}")

    # ------------------------------------------------------------------ 检查项

    def _check_secret_key(self):
        secret = settings.SECRET_KEY or ""
        auto_file = Path(settings.BASE_DIR) / "data" / ".secret_key"
        if not secret:
            self._report(
                FAIL,
                "配置与密钥",
                "SECRET_KEY 为空（JWT 签名与字段加密不可用）",
                "在 config.yml 或环境变量设置 SECRET_KEY",
            )
            return
        if auto_file.is_file() and auto_file.read_text(encoding="utf8").strip() == secret:
            mode = stat.S_IMODE(auto_file.stat().st_mode)
            detail = f"使用自动生成的开发密钥（{auto_file.relative_to(settings.BASE_DIR)}，权限 {oct(mode)}）"
            if mode != 0o600:
                self._report(WARN, "配置与密钥", f"{detail}；建议收紧权限", f"chmod 600 {auto_file}")
            else:
                self._report(
                    WARN, "配置与密钥", f"{detail}；仅限开发/体验", "生产：config.yml 或环境变量显式配置 SECRET_KEY"
                )
        else:
            self._report(PASS, "配置与密钥", "SECRET_KEY 已显式配置")

    def _check_database(self):
        from django.db import connection

        try:
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            self._report(PASS, "数据库", f"连接正常（{connection.vendor} / {connection.settings_dict.get('NAME')}）")
        except Exception as exc:
            self._report(FAIL, "数据库", f"连接失败：{exc}", "检查 config.yml 的 DB_* 与数据库服务状态")

    def _check_redis(self):
        from django.core.cache import cache

        try:
            token = str(time.time())
            cache.set(CACHE_PROBE_KEY, token, 10)
            value = cache.get(CACHE_PROBE_KEY)
            cache.delete(CACHE_PROBE_KEY)
            if value == token:
                self._report(PASS, "Redis", "缓存读写正常")
            else:
                self._report(
                    FAIL,
                    "Redis",
                    "缓存读回不一致（IGNORE_EXCEPTIONS 可能吞掉了连接错误）",
                    "检查 config.yml 的 REDIS_* 与 Redis 服务状态",
                )
        except Exception as exc:
            self._report(FAIL, "Redis", f"连接失败：{exc}", "检查 config.yml 的 REDIS_* 与 Redis 服务状态")

    def _check_locale(self):
        base = Path(settings.BASE_DIR)
        po_files = sorted(base.glob("locale/*/LC_MESSAGES/django.po"))
        if not po_files:
            self._report(PASS, "语言包", "未找到 locale/*/LC_MESSAGES/django.po（跳过）")
            return
        stale = []
        for po in po_files:
            mo = po.with_suffix(".mo")
            if not mo.is_file() or mo.stat().st_mtime < po.stat().st_mtime:
                # 语言码 = locale/<lang>/LC_MESSAGES/django.po 的 <lang> 段
                stale.append(po.parent.parent.name)
        if stale:
            self._report(
                WARN,
                "语言包",
                f"缺失或未更新：{', '.join(stale)}（界面文案将回退英文）",
                "python manage.py compilemessages",
            )
        else:
            self._report(PASS, "语言包", f"{len(po_files)} 个语言包已编译")

    def _check_permissions(self):
        try:
            from system.models import UserInfo

            if not UserInfo.objects.exists():
                self._report(WARN, "权限点", "数据库尚未初始化（无用户）", "python utils/init_data.py")
                return
            from system.utils import permission_sync as sync

            gaps = sync.scan_permission_gaps()
            if gaps:
                self._report(
                    WARN,
                    "权限点",
                    f"检测到 {len(gaps)} 条缺口（非超管将 403）",
                    "python manage.py sync_menu_permissions",
                )
                for route, method, _action in gaps[:DETAIL_LIMIT]:
                    self.stdout.write(f"        · {method:<6s} {route.url}")
                if len(gaps) > DETAIL_LIMIT:
                    self.stdout.write(f"        · ……其余 {len(gaps) - DETAIL_LIMIT} 条略")
            else:
                self._report(PASS, "权限点", "代码路由与库内权限点一致")
        except Exception as exc:
            self._report(WARN, "权限点", f"扫描失败（跳过）：{exc}")

    def _check_modules(self):
        try:
            from common.core.modules import modules_report, override_active, validate_deployment_config

            resolution = validate_deployment_config()
            report = modules_report(resolution)
            enabled = sum(1 for item in report if item["enabled"])
            detail = f"preset={resolution.preset}，启用 {enabled}/{len(report)} 个模块"
            # overrides 是「显式 MODULE_ENABLE/MODULE_DISABLE 条目」（可来自 config.yml /
            # 环境变量 / 后台覆盖行），本身是合法配置选择，不是问题；只有后台覆盖行
            # （DB 单行，会盖住 config.yml 基线）才需要提示清理命令。
            if resolution.overrides:
                detail += f"；显式增删 {len(resolution.overrides)} 条（{', '.join(resolution.overrides)}）"
            if override_active():
                self._report(
                    WARN,
                    "模块裁剪",
                    f"{detail}；存在后台覆盖行（当前生效组合来自覆盖行，非 config.yml 基线）",
                    "python manage.py modules --clear-override（清理覆盖，回到 config.yml 基线）",
                )
            else:
                self._report(PASS, "模块裁剪", detail)
        except Exception as exc:
            self._report(
                FAIL,
                "模块裁剪",
                f"配置非法：{exc}",
                "检查 config.yml 的 MODULE_PRESET / MODULE_ENABLE / MODULE_DISABLE",
            )

    def _check_ai_declarations(self):
        """生成物自检：`<app>/ai_declarations.py` 的声明路径必须能对上路由面。

        - 无声明文件：跳过（不是所有模块都需要 AI 化）；
        - 声明路径无法 resolve：失败（生成物与会话/路由漂移，AI 工具目录会指向不存在的端点）。
        """
        from django.apps import apps as django_apps
        from django.urls import Resolver404, resolve
        from django.utils.module_loading import import_string

        from common.swagger.ai_meta import normalize_path

        checked, broken, modules = 0, [], []
        for config in django_apps.get_app_configs():
            module_path = f"{config.name}.ai_declarations"
            try:
                module = import_string(module_path)
            except ImportError:
                continue
            except Exception as exc:  # noqa: BLE001 声明文件本身导入失败即生成物坏了
                broken.append(f"{module_path}: {exc}")
                continue
            modules.append(config.name)
            for name in dir(module):
                if not name.endswith("_ACTIONS"):
                    continue
                specs = getattr(module, name)
                for spec in specs.values() if isinstance(specs, dict) else specs:
                    path = normalize_path(getattr(spec, "path", ""))
                    if not path:
                        continue
                    checked += 1
                    try:
                        resolve(path.lstrip("/").replace("<pk>", "1"))
                    except Resolver404:
                        broken.append(f"{module_path}::{getattr(spec, 'key', path)} -> {path}")
        if broken:
            self._report(
                FAIL,
                "AI 声明（生成物）",
                f"{len(broken)} 条声明对不上路由：{broken[:3]}",
                "跑 generate_crud 重新生成，或修正声明 path（口径见 system/utils/ai_api_actions.py）",
            )
        elif checked:
            self._report(PASS, "AI 声明（生成物）", f"{len(checked)} 条声明路径可解析（模块 {len(modules)} 个）")
        else:
            self._report(PASS, "AI 声明（生成物）", "无声明文件（非 AI 化模块，跳过）")

    def _check_frontend_contract(self):
        local = Path(settings.BASE_DIR) / "docs" / "schema"
        front = Path(settings.BASE_DIR).parent / "xadmin-client" / "contract" / "schema"
        if not front.is_dir():
            self._report(PASS, "契约镜像", "未检出前端仓库（xadmin-client），跳过")
            return
        local_files = {p.name: p for p in local.glob("*.json")}
        front_files = {p.name: p for p in front.glob("*.json")}
        common = sorted(set(local_files) & set(front_files))
        mismatched = []
        for name in common:
            try:
                if json.loads(local_files[name].read_text(encoding="utf8")) != json.loads(
                    front_files[name].read_text(encoding="utf8")
                ):
                    mismatched.append(name)
            except Exception as exc:
                mismatched.append(f"{name}（解析失败：{exc}）")
        missing = sorted(set(local_files) - set(front_files))
        if mismatched or missing:
            detail = "不一致：" + "、".join(mismatched) if mismatched else "前端缺少：" + "、".join(missing)
            self._report(
                WARN,
                "契约镜像",
                f"后端 docs/schema 与前端 contract/schema {detail}",
                "同步 schema 后运行 pnpm check:contract",
            )
        else:
            self._report(PASS, "契约镜像", f"{len(common)} 个 schema 与前端一致")

    def _check_version_sync(self):
        """前后端版本一致性（server/const.py VERSION ↔ xadmin-client/package.json）。

        单仓检出 / 容器内（同工作区无前端仓库）时跳过；不一致只告警不判失败
        （版本号可能在两仓 PR 之间短暂领先），发布侧另有 tag 门禁强制拦截
        （.github/workflows/build-image.yml 的 check-version）。
        """
        from server.const import VERSION

        client_pkg = Path(settings.BASE_DIR).parent / "xadmin-client" / "package.json"
        if not client_pkg.is_file():
            self._report(PASS, "版本一致", "未检出前端仓库（xadmin-client），跳过")
            return
        try:
            client_version = json.loads(client_pkg.read_text(encoding="utf8")).get("version", "")
        except Exception as exc:  # noqa: BLE001 自检项不因读取失败而中断
            self._report(WARN, "版本一致", f"前端 package.json 解析失败：{exc}")
            return
        if client_version == VERSION:
            self._report(PASS, "版本一致", f"前后端版本一致（{VERSION}）")
        else:
            self._report(
                WARN,
                "版本一致",
                f"后端 {VERSION} vs 前端 {client_version or '未知'}",
                "同步修改 server/const.py 与 xadmin-client/package.json（发布 tag 门禁会强校验）",
            )
