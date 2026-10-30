#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SQL 抽样实测：含 SerializerMethodField 的 list 端点每请求 SQL 数。

触发制任务（NEXT-DEV-PLAN §一 / docs/plans/触发制任务清单-长期.md）的实测工具：
自动发现「序列化器声明了 SerializerMethodField」的 list 端点，在种子数据上按两档页宽
（5 / 40 行）实测每请求 SQL 数。SQL 数随行数线性增长（斜率 ≈ 每行额外查询数）即 N+1
特征，配合高频 SQL 指纹可直接定位到具体取数路径。

测量口径：``CaptureQueriesContext``（强制 debug 游标，与 SQLCountMiddleware 的
``connection.queries`` 同源；DEBUG=False 也可用，无需起服务）。

用法（依赖 compose.test.yml 的 PG+Redis，与 pytest 真环境档同库参数）::

    docker compose -f compose.test.yml up -d
    DJANGO_SETTINGS_MODULE=tests.settings_real .venv/bin/python scripts/sql_sampling.py
    # 首次跑完建了测试库，之后 --keepdb 复用（跳过 migrate）
    DJANGO_SETTINGS_MODULE=tests.settings_real .venv/bin/python scripts/sql_sampling.py --keepdb
    # 输出 JSON 供台账记录
    ... scripts/sql_sampling.py --keepdb --json /tmp/sql_sampling.json

退出码：0 = 未见 N+1 特征（斜率 < 0.5）；1 = 存在可疑端点；2 = 环境不可用。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)  # 允许以脚本方式直跑（import tests.settings_real / sql_sampling_seeds）

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings_real")

import django  # noqa: E402
from sql_sampling_seeds import reset_tables, seed  # noqa: E402 伴生模块（种子数据，脚本方式直跑需先入 sys.path）

django.setup()

from django.db import connection  # noqa: E402
from django.test.utils import CaptureQueriesContext, setup_test_environment  # noqa: E402
from django.urls import get_resolver  # noqa: E402
from rest_framework import serializers  # noqa: E402
from rest_framework.test import APIClient  # noqa: E402

setup_test_environment()  # ALLOWED_HOSTS += testserver（与 pytest-django 同款注入）

PAGE_SMALL, PAGE_LARGE = 5, 40
SLOPE_THRESHOLD = 0.5  # 每行额外 SQL 数达到该值即判定 N+1 特征
LITERAL_RE = re.compile(r"'[^']*'|\"[^\"]*\"|\b\d+\b")


@dataclass
class Sample:
    """单个端点的抽样结果。"""

    path: str
    action: str
    serializer: str
    method_fields: list[str]
    status_small: int = 0
    status_large: int = 0
    sql_small: int = -1
    sql_large: int = -1
    rows_large: int = -1
    note: str = ""
    fingerprints: list = field(default_factory=list)

    @property
    def slope(self) -> float:
        if self.sql_small < 0 or self.sql_large < 0:
            return 0.0
        return (self.sql_large - self.sql_small) / (PAGE_LARGE - PAGE_SMALL)

    @property
    def sufficient_data(self) -> bool:
        # 大页未铺满说明行数不足，斜率测不出（不判干净，只判本次不充分）
        return self.rows_large >= PAGE_LARGE

    @property
    def suspect(self) -> bool:
        return self.sql_large >= 0 and self.sufficient_data and self.slope >= SLOPE_THRESHOLD


def _pattern_text(pattern) -> str:
    """取 URLResolver / URLPattern 自身的路径文本（str(节点) 是带尖括号的 repr，不能直接用）。"""
    text = str(pattern.pattern)
    return text.lstrip("^").rstrip("$")


def _serializer_of(view_cls, action: str):
    """取视图在指定 action 下的序列化器实例字段（与 BaseViewSet.get_serializer_related_fields 同法）。"""
    instance = view_cls()
    instance.action = action
    get_serializer_class = getattr(instance, "get_serializer_class", None)
    serializer_class = get_serializer_class() if get_serializer_class else getattr(view_cls, "serializer_class", None)
    if serializer_class is None:
        return None
    return serializer_class()


def discover() -> list[Sample]:
    """遍历 URLconf，找出 list action 序列化器含 SerializerMethodField 的端点。"""
    found: list[Sample] = []
    seen = set()
    resolver = get_resolver()

    def walk(patterns, prefix: str):
        for pattern in patterns:
            path = prefix + _pattern_text(pattern)
            if hasattr(pattern, "url_patterns"):
                walk(pattern.url_patterns, path)
                continue
            if "<" in path or "(" in path or "?" in path:
                continue  # 带 pk / 参数占位的路由不在本次抽样范围（list 型路由无占位）
            callback = pattern.callback
            view_cls = getattr(callback, "cls", None)
            actions = getattr(callback, "actions", None) or {}
            action = actions.get("get")
            if view_cls is None or action != "list":
                continue
            try:
                serializer = _serializer_of(view_cls, action)
                fields = serializer.fields if serializer is not None else {}
            except Exception as exc:  # noqa: BLE001 定制视图取序列化器失败按跳过登记
                found.append(
                    Sample(
                        path=f"/{path}",
                        action=action,
                        serializer=f"{view_cls.__name__}",
                        method_fields=[],
                        note=f"serializer 不可解析：{type(exc).__name__}: {exc}",
                    )
                )
                continue
            method_fields = [name for name, f in fields.items() if isinstance(f, serializers.SerializerMethodField)]
            if not method_fields:
                continue
            key = f"/{path}"
            if key in seen:
                continue
            seen.add(key)
            found.append(
                Sample(
                    path=key,
                    action=action,
                    serializer=f"{serializer.__class__.__module__.rsplit('.', 1)[-1]}.{serializer.__class__.__name__}",
                    method_fields=method_fields,
                )
            )

    walk(resolver.url_patterns, "")
    found.sort(key=lambda s: s.path)
    return found


# 需要 query 参数才能返回 200 的端点（按路径子串匹配，值在运行期从种子数据取）
def param_overrides() -> dict[str, dict]:
    overrides = {}
    try:
        from dataset.models import DynamicForm

        form_pk = str(DynamicForm.objects.first().pk)
        overrides["dataset/form-data"] = {"form": form_pk}
    except Exception:  # noqa: BLE001 采样脚本：无演示表单时跳过该覆盖项
        pass
    return overrides


def fingerprint(queries: list[str], min_repeat: int = 5, top: int = 3) -> list:
    """SQL 指纹（字面量归一）按重复次数排序，重复 ≥ min_repeat 的视为疑似逐行查询。"""
    counter = Counter(LITERAL_RE.sub("?", q[:300]) for q in queries)
    return [(sql, count) for sql, count in counter.most_common(top) if count >= min_repeat]


def _rendered_rows(response) -> int:
    """从响应包取本页渲染行数（分页包 total 或 results 长度，取大页口径）。"""
    data = getattr(response, "data", None)
    payload = data.get("data") if isinstance(data, dict) else None
    if isinstance(payload, dict):
        total = payload.get("total")
        if total is not None:
            return int(total)
        results = payload.get("results") or payload.get("list")
        if isinstance(results, list):
            return len(results)
    if isinstance(payload, list):
        return len(payload)
    return 0


def measure(sample: Sample, client: APIClient, overrides: dict) -> None:
    params = {}
    for key, value in overrides.items():
        if key in sample.path:
            params.update(value)
    warm = client.get(sample.path, {**params, "size": PAGE_SMALL})
    sample.status_small = warm.status_code
    if warm.status_code != 200:
        detail = getattr(warm, "data", None)
        sample.note = f"size={PAGE_SMALL} 非 200：{json.dumps(detail, ensure_ascii=False, default=str)[:200]}"
        return
    with CaptureQueriesContext(connection) as ctx_small:
        client.get(sample.path, {**params, "size": PAGE_SMALL})
    sample.sql_small = len(ctx_small.captured_queries)
    with CaptureQueriesContext(connection) as ctx_large:
        resp_large = client.get(sample.path, {**params, "size": PAGE_LARGE})
    sample.sql_large = len(ctx_large.captured_queries)
    sample.status_large = resp_large.status_code
    sample.rows_large = _rendered_rows(resp_large)
    if resp_large.status_code != 200:
        sample.note = f"size={PAGE_LARGE} 非 200：{json.dumps(getattr(resp_large, 'data', None), ensure_ascii=False, default=str)[:200]}"
    sample.fingerprints = fingerprint([q["sql"] for q in ctx_large.captured_queries])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keepdb", action="store_true", help="复用已存在的测试库（跳过建库与 migrate）")
    parser.add_argument("--json", type=str, default="", help="结果 JSON 输出路径")
    args = parser.parse_args()

    from django.core.cache import cache

    print("== SQL 抽样实测 ==")
    print(f"库：{connection.settings_dict['NAME']}（--keepdb 复用）")
    connection.creation.create_test_db(keepdb=args.keepdb)
    cache.clear()

    reset_tables()
    covered = seed()
    print(f"种子：{covered}")

    samples = discover()
    print(f"候选端点（serializer 含 SerializerMethodField 的 list）：{len(samples)} 个")
    unresolvable = [s for s in samples if s.note]
    if unresolvable:
        for s in unresolvable:
            print(f"  [skip] {s.path}: {s.note}")
    samples = [s for s in samples if not s.note]

    from django.contrib.auth import get_user_model

    admin = get_user_model().objects.get(username="admin")
    client = APIClient(HTTP_USER_AGENT="sql-sampling")
    client.force_authenticate(user=admin)

    overrides = param_overrides()
    for sample in samples:
        measure(sample, client, overrides)

    suspects = [s for s in samples if s.suspect]
    print(f"\n{'端点':58} {'serializer':44} {'SQL@5':>6} {'SQL@40':>7} {'行@40':>6} {'斜率':>6}  判定")
    for s in samples:
        if s.suspect:
            mark = "⚠ N+1"
        elif s.sql_large < 0:
            mark = "ERR"
        elif not s.sufficient_data:
            mark = "数据不足"
        else:
            mark = "--"
        print(
            f"{s.path[:58]:58} {s.serializer[:44]:44} {s.sql_small:>6} {s.sql_large:>7} {s.rows_large:>6} {s.slope:>6.2f}  {mark} {s.note}"
        )
        for sql, count in s.fingerprints:
            print(f"    ×{count:<4} {sql[:180]}")

    insufficient = [s for s in samples if s.sql_large >= 0 and not s.sufficient_data and not s.suspect]
    print(
        f"\n结论：{len(suspects)} 个端点存在 N+1 特征（斜率 ≥ {SLOPE_THRESHOLD}）；另有 {len(insufficient)} 个端点数据不足整页，仅参考"
    )
    for s in suspects:
        print(f"  - {s.path} {s.method_fields}")

    if args.json:
        payload = {
            "instrument": "CaptureQueriesContext (debug cursor, 与 SQLCountMiddleware 同源)",
            "page_sizes": [PAGE_SMALL, PAGE_LARGE],
            "seed": covered,
            "samples": [
                {
                    "path": s.path,
                    "serializer": s.serializer,
                    "method_fields": s.method_fields,
                    "sql_small": s.sql_small,
                    "sql_large": s.sql_large,
                    "slope": round(s.slope, 2),
                    "suspect": s.suspect,
                    "status": [s.status_small, s.status_large],
                    "note": s.note,
                    "fingerprints": [[sql, c] for sql, c in s.fingerprints],
                }
                for s in samples
            ],
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"JSON 已写入 {args.json}")

    return 1 if suspects else 0


if __name__ == "__main__":
    sys.exit(main())
