#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : dump_init_json
# author : ly_13
# date : 12/25/2023
import json
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch

from identity.models import DeptInfo, LoginAccessPolicy, UserRole
from settings.models import Setting
from system.models import *
from system.services.seeding import discover, platform_file
from system.services.seeding.split import bucket_rows, write_buckets


def get_fields(model: Any) -> Any:
    if issubclass(model, FieldPermission):
        exclude_fields = ["updated_time", "created_time"]
    elif issubclass(model, ModelLabelField):
        exclude_fields = ["updated_time"]
    elif issubclass(model, UserRole):
        # 角色授权已改为各层 seeds/grants.json 的声明式并集：导出角色行时必须剔除 m2m，
        # 否则一次 dump 就会把覆盖式菜单列表写回种子，下次装载又把其它层的授权清零。
        exclude_fields = ["menu"]
    else:
        exclude_fields = []

    return [x.name for x in model._meta.get_fields() if x.name not in exclude_fields]


class Command(BaseCommand):
    help = "dump init json data"

    model_names: list[Any] = [
        UserRole,
        DeptInfo,
        Menu,
        MenuMeta,
        SystemConfig,
        DataPermission,
        FieldPermission,
        ModelLabelField,
        LoginAccessPolicy,
        Setting,
    ]

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--split-apps",
            action="store_true",
            help="按应用归属导出到各层（平台层 loadjson/ + 各应用 seeds/），默认全量",
        )
        parser.add_argument(
            "--apps",
            default="",
            help="配合 --split-apps：只导出这些应用（逗号分隔）；平台层共享行始终包含",
        )
        parser.add_argument(
            "--output",
            default="",
            help="导出根目录（默认 PROJECT_DIR；用于生成补丁或测试）",
        )

    def save_json(self, queryset: Any, filename: Any) -> None:
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        fields = get_fields(queryset.model)
        # 序列化器对「每个对象的每个 m2m」各发一条查询（handle_m2m_field 读 related
        # manager）：714 个菜单就是 700+ 条、516 个字段权限又是 500+ 条小查询。PG 一旦
        # 出现小查询延迟退化（满载/checkpoint 时实测单条墙钟 ~1s，同条 EXPLAIN 仅
        # 0.6ms），一次导出就爬行到分钟级——全量串行跑时表现为「卡在一个用例里永不结束」。
        # 先 prefetch，序列化器命中 _prefetched_objects_cache 后不再逐对象查库。
        # 预取 queryset 与序列化器逐对象查询（``related`` + select_related(None) + only("pk")）
        # 同形，保证导出内容与顺序逐字节不变。关系列表按主键排序：模型默认排序多是
        # ``-created_time``，而种子行该列为 NULL（loaddata 跳过 pre_save），等值键的
        # 排序不稳定——同一份数据连续两次导出会得到不同的 m2m 顺序，破坏「导出幂等」。
        m2m_fields = [
            field for field in queryset.model._meta.local_many_to_many if field.serialize and field.attname in fields
        ]
        if m2m_fields:
            queryset = queryset.prefetch_related(
                *[
                    Prefetch(
                        field.name,
                        queryset=field.related_model._default_manager.select_related(None).only("pk").order_by("pk"),
                    )
                    for field in m2m_fields
                ]
            )
        stream = open(filename, "w", encoding="utf8")
        try:
            serializers.serialize(
                "json",
                queryset,
                indent=2,
                stream=stream or self.stdout,
                object_count=queryset.count(),
                fields=fields,
            )
        except Exception as e:
            print(f"{queryset.model._meta.model_name} {filename} dump failed {e}")
        finally:
            if stream:
                stream.close()

    def handle(self, *args: Any, **options: Any) -> None:
        if options.get("split_apps"):
            self._dump_split(options.get("apps") or "", options.get("output") or "")
            return
        for model in self.model_names:
            self.save_json(model.objects.all().order_by("pk"), platform_file(f"{model._meta.model_name}.json"))
        # 不分层时只写平台层。同主键在应用层也存在时，装载顺序会让应用层的旧行覆盖刚导出的内容。
        self.stdout.write(
            self.style.WARNING(
                "已导出到平台层；请运行 python manage.py seed_split 按应用归属重新分层，"
                "或改用 --split-apps 直接分层导出"
            )
        )

    def _dump_split(self, raw_apps: str, raw_output: str) -> None:
        discovery = discover()
        selected = self._parse_apps(raw_apps, discovery.manifests)
        manifests = {**discovery.manifests}
        if selected:
            target_root = Path(raw_output) if raw_output else Path(settings.PROJECT_DIR)
            for app in selected:
                if app in manifests:
                    continue
                manifests[app] = self._init_manifest(app, target_root)

        rows_by_model: dict[str, list[Any]] = {}
        with tempfile.TemporaryDirectory(prefix="xadmin_dump_") as tmp:
            for model in self.model_names:
                path = Path(tmp) / f"{model._meta.model_name}.json"
                self.save_json(model.objects.all().order_by("pk"), path)
                rows_by_model[model._meta.label_lower] = json.loads(path.read_text(encoding="utf-8"))

        buckets = bucket_rows(rows_by_model, manifests, selected=selected)
        written = write_buckets(buckets, root=raw_output or None, manifests=manifests)

        total = sum(len(rows) for rows in rows_by_model.values())
        self.stdout.write(
            f"按应用导出完成：{'、'.join(sorted(selected)) if selected else '全部应用'}；"
            f"共 {total} 行 → {len(written)} 个文件"
        )
        for path in written:
            self.stdout.write(f"  {path}")
        if selected:
            self.stdout.write(
                self.style.WARNING("平台层共享行（角色/部门/站点配置/数据权限）始终导出，便于补丁直接装载")
            )

    def _init_manifest(self, app_label: str, target_root: Path) -> Any:
        """为「已注册但还没有 seeds/manifest.json」的应用初始化清单（幂等）。

        ``url_prefixes`` 从路由反查（视图模块前缀 == app label），它是权限点归属的判定依据；
        推导为空时保留空数组并告警，避免瞎猜一个前缀把别的应用的行吸过来。
        """
        from django.apps import apps as django_apps

        from system.services.seeding import SeedManifest

        try:
            django_apps.get_app_config(app_label)
        except LookupError as exc:
            raise CommandError(
                f"未知应用：{app_label}（既没有 seeds/manifest.json，也不是已注册的 Django 应用）"
            ) from exc

        prefixes = _app_url_prefixes(app_label)
        path = target_root / app_label / "seeds" / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "app": app_label,
                    "depends": ["system", "identity"],
                    "url_prefixes": list(prefixes),
                    "files": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.stdout.write(f"已初始化应用种子清单：{path}")
        if not prefixes:
            self.stdout.write(
                self.style.WARNING(
                    f"{app_label} 的 url_prefixes 未能从路由推导（请手工补充），否则其权限点会被归到平台层"
                )
            )
        return SeedManifest(app=app_label, depends=("system", "identity"), url_prefixes=prefixes)

    @staticmethod
    def _parse_apps(raw: str, manifests: dict[str, Any]) -> list[str] | None:
        if not raw:
            return None
        selected = [item.strip() for item in raw.split(",") if item.strip()]
        unknown = [app for app in selected if app not in manifests and not _is_local_app(app)]
        if unknown:
            raise CommandError(f"未知应用（缺少 seeds/manifest.json 且未注册）：{'、'.join(unknown)}")
        return selected


def _is_local_app(app_label: str) -> bool:
    """是否为本仓库内注册的本地应用（跨仓/三方应用不允许隐式生成清单）。"""

    from django.apps import apps as django_apps

    try:
        config = django_apps.get_app_config(app_label)
    except LookupError:
        return False
    root = Path(settings.PROJECT_DIR).resolve()
    try:
        Path(config.path).resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _app_url_prefixes(app_label: str) -> tuple[str, ...]:
    """从路由反查应用占用的 ``api/<segment>/`` 前缀（权限点归属判定依据）。"""

    from system.services.permission_sync import build_route_index

    prefixes: set[str] = set()
    for route in build_route_index():
        view = str(getattr(route, "view", ""))
        if not view.startswith(f"{app_label}."):
            continue
        segments = str(getattr(route, "url", "")).strip("/").split("/")
        if len(segments) >= 2 and segments[0] == "api":
            prefixes.add(f"api/{segments[1]}/")
    return tuple(sorted(prefixes))
