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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 允许以脚本方式直跑（import tests.settings_real）

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings_real")

import django  # noqa: E402

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


def reset_tables() -> None:
    """专用一次性测试库全量清表（--keepdb 复跑时保证播种幂等）。"""
    with connection.cursor() as cursor:
        tables = [t for t in connection.introspection.table_names() if t != "django_migrations"]
        if tables:
            quoted = ", ".join(f'"{t}"' for t in tables)
            cursor.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")


def _ensure_admin():
    from django.contrib.auth import get_user_model

    admin = get_user_model().objects.filter(username="admin").first()
    if admin is None:
        admin = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="Admin@123456"
        )
    return admin


def _seed_system_core(admin, covered: dict) -> list:
    """用户（挂部门/角色/岗位）+ 标签 + 字典 + 上传文件。"""
    from django.contrib.contenttypes.models import ContentType

    from identity.models import DeptInfo, Post, UserInfo, UserRole
    from system.models import DataDict, Tag, TaggedItem, UploadFile

    roles = [UserRole.objects.create(name=f"角色{i}", code=f"role{i}") for i in range(45)]
    depts = [DeptInfo.objects.create(name=f"部门{i}", code=f"dept{i}") for i in range(45)]
    posts = [Post.objects.create(name=f"岗位{i}", code=f"post{i}", dept=depts[i % len(depts)]) for i in range(45)]
    users = []
    for i in range(45):
        user = UserInfo.objects.create_user(username=f"sample_{i:02d}", password="Test@123456", nickname=f"样本{i}")
        user.dept = depts[i % len(depts)]
        user.save(update_fields=["dept"])
        user.roles.add(roles[i % len(roles)])
        user.posts.add(posts[i % len(posts)])
        users.append(user)
    covered["system/user"] = len(users)

    try:
        content_type = ContentType.objects.get_for_model(type(admin))
        for tag in [Tag.objects.create(name=f"标签{i}") for i in range(45)]:
            TaggedItem.objects.create(tag=tag, object_id=str(admin.pk), content_type=content_type)
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] tag 绑定跳过：{exc}")

    try:
        roots = [DataDict.objects.create(code=f"group{i}", label=f"字典组{i}") for i in range(45)]
        for root in roots:
            for j in range(8):
                DataDict.objects.create(code=f"{root.code}_{j}", label=f"字典项{j}", parent=root)
        covered["system/dict"] = 45 * 8
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] dict 跳过：{exc}")

    try:
        UploadFile.objects.bulk_create(
            [
                UploadFile(
                    filename=f"样本文件{i}.txt",
                    filesize=1024 * i,
                    mime_type="text/plain",
                    md5sum=f"md5-sample-{i:04d}",
                    creator=users[i % len(users)],
                )
                for i in range(45)
            ]
        )
        covered["system/upload"] = 45
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] upload 跳过：{exc}")
    return users, depts, roles, posts


def _seed_system_extended(users, admin, covered: dict) -> None:
    """任务执行记录 / 个人访问令牌 / 数据权限 / 登录日志。"""
    from identity.models import PersonalAccessToken
    from system.models import DataPermission, TaskExecution, UserLoginLog

    try:
        TaskExecution.objects.bulk_create(
            [TaskExecution(name=f"抽样任务{i}", creator=users[i % len(users)]) for i in range(45)]
        )
        covered["system/task"] = 45
        PersonalAccessToken.objects.bulk_create(
            [
                PersonalAccessToken(
                    name=f"令牌{i}", token_hash=f"hash-{i:04d}", token_prefix=f"tp{i:02d}", creator=admin
                )
                for i in range(45)
            ]
        )
        covered["system/token"] = 45
        DataPermission.objects.bulk_create(
            [DataPermission(name=f"数据权限{i}", rules=[], creator=users[i % len(users)]) for i in range(45)]
        )
        covered["system/permission"] = 45
        logs = []
        for i in range(45):
            log = UserLoginLog(creator=users[i % len(users)], ipaddress=f"10.0.0.{i % 250}", agent="sql-sampling")
            if i % 5 == 0:
                log.login_type = UserLoginLog.LoginTypeChoices.WEBSOCKET
                log.channel_name = f"ws-{i}"
            logs.append(log)
        UserLoginLog.objects.bulk_create(logs)
        covered["system/login-log"] = 45
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] system 扩展表跳过：{exc}")


def _seed_notifications(users, admin, depts, roles, posts, covered: dict) -> None:
    """站内信（USER 型 40 条）+ 部门/角色/岗位公告各 1 条。"""
    try:
        from notifications.models import MessageContent

        for i in range(40):
            msg = MessageContent.objects.create(
                title=f"站内信样本{i}", message="正文", notice_type=MessageContent.NoticeChoices.USER, creator=admin
            )
            msg.notice_user.add(users[i % len(users)])
        for kind, related in (
            (MessageContent.NoticeChoices.DEPT, "notice_dept"),
            (MessageContent.NoticeChoices.ROLE, "notice_role"),
            (MessageContent.NoticeChoices.POST, "notice_post"),
        ):
            msg = MessageContent.objects.create(title=f"{kind}公告", message="正文", notice_type=kind, creator=admin)
            getattr(msg, related).add(
                depts[0]
                if kind == MessageContent.NoticeChoices.DEPT
                else (roles[0] if kind == MessageContent.NoticeChoices.ROLE else posts[0])
            )
        covered["notifications/message"] = 43
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] notifications 跳过：{exc}")


def _seed_dataset(users, admin, covered: dict) -> None:
    try:
        from dataset.models import Dataset, DynamicForm, DynamicFormSubmission

        for i in range(45):
            Dataset.objects.create(name=f"数据集{i}", bound_model="identity.UserInfo", creator=admin)
        forms = [DynamicForm.objects.create(name=f"表单{i}", schema=[], creator=admin) for i in range(2)]
        DynamicFormSubmission.objects.bulk_create(
            [DynamicFormSubmission(form=forms[0], data={"k": i}, creator=users[i % len(users)]) for i in range(45)]
        )
        covered["dataset"] = 45
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] dataset 跳过：{exc}")


def _seed_approval(users, admin, covered: dict) -> None:
    """流程 + 实例 + 请假单 + 操作审批（ApprovalRequest）+ 审批规则。"""
    try:
        from approval.models.approval import ApprovalFlow, ApprovalFlowNode
        from approval.utils.approval_flow import create_instance

        flows = []
        for i in range(45):
            flow = ApprovalFlow.objects.create(
                name=f"抽样流程{i}", code=f"sql_sampling_{i}", form_schema=[], is_active=True
            )
            ApprovalFlowNode.objects.create(
                flow=flow,
                name="初审",
                order=1,
                approve_type=ApprovalFlowNode.ApproveType.OR,
                assignee_type=ApprovalFlowNode.AssigneeType.USER,
                assignee_value=admin.username,
                condition={},
                timeout_hours=0,
            )
            flows.append(flow)
        instances = []
        for i in range(45):
            instance, error = create_instance(
                flow=flows[i], applicant=users[i % len(users)], title=f"抽样申请{i}", form_data={}
            )
            if error is None:
                instances.append(instance)
        covered["approval/instance"] = len(instances)

        try:
            from datetime import timedelta

            from django.utils import timezone

            from approval.models.leave import Leave

            base = timezone.localdate()
            for instance in instances:
                Leave.objects.create(
                    instance=instance,
                    start_date=base,
                    end_date=base + timedelta(days=1),
                    days=1,
                    reason="抽样",
                    creator=instance.creator,
                )
            covered["approval/leave"] = len(instances)
        except Exception as exc:  # noqa: BLE001
            print(f"  [seed] leave 跳过：{exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] approval 跳过：{exc}")
        return

    try:
        from approval.models.approval_request import ApprovalRequest
        from approval.models.approval_rule import ApprovalRule

        for i in range(45):
            ApprovalRule.objects.create(name=f"审批规则{i}", path_patterns=["^api/system/"], creator=admin)
        requests_ = [
            ApprovalRequest(method="GET", path=f"api/system/user/{i}", approver=admin, creator=users[i % len(users)])
            for i in range(45)
        ]
        ApprovalRequest.objects.bulk_create(requests_)
        for request_ in requests_:
            request_.current_assignees.set([users[(request_.path.count("u") + 1) % len(users)], admin])
        covered["approval/request"] = 45
        covered["approval/rule"] = 45
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] approval request/rule 跳过：{exc}")


def _seed_ai(admin, covered: dict) -> None:
    try:
        from ai.models.ai import AiKnowledgeDocument, AiProfile
        from ai.models.mcp import McpServer

        for i in range(45):
            AiProfile.objects.create(name=f"模型画像{i}", base_url="https://api.example.com/v1", creator=admin)
            McpServer.objects.create(name=f"MCP{i}", url="https://mcp.example.com", creator=admin)
            AiKnowledgeDocument.objects.create(path=f"kb/sample-{i:02d}.md", title=f"知识文档{i}", creator=admin)
        covered["ai"] = 135
    except Exception as exc:  # noqa: BLE001
        print(f"  [seed] ai 跳过：{exc}")


def seed() -> dict:
    """按抽样面播种代表量数据（块间防御式隔离：单块失败不阻断其余端点抽样）。

    返回各域播种行数（covered），随报告输出供评估数据充分性。
    """
    covered: dict[str, int] = {}
    admin = _ensure_admin()
    users, depts, roles, posts = _seed_system_core(admin, covered)
    _seed_system_extended(users, admin, covered)
    _seed_notifications(users, admin, depts, roles, posts, covered)
    _seed_dataset(users, admin, covered)
    _seed_approval(users, admin, covered)
    _seed_ai(admin, covered)
    return covered


# 需要 query 参数才能返回 200 的端点（按路径子串匹配，值在运行期从种子数据取）
def param_overrides() -> dict[str, dict]:
    overrides = {}
    try:
        from dataset.models import DynamicForm

        form_pk = str(DynamicForm.objects.first().pk)
        overrides["dataset/form-data"] = {"form": form_pk}
    except Exception:  # noqa: BLE001
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
