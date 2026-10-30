# -*- coding: utf-8 -*-
"""副作用 GET 清单门禁。

Cookie 承载 JWT 是无感刷新/WS 认证所必需的通道，SameSite=Lax 已封住跨站 POST；
剩余风险面是「名为 GET、实际改状态」的 action——浏览器跨站 GET 会携带 Cookie，
一旦存在副作用就会被 CSRF 利用。当前全仓没有此类端点；本测试用 AST 扫描
``@action(methods=["get"])`` 内的持久化/外发调用，新增副作用 GET 必须改 POST，
或在 ``ALLOWED_SIDE_EFFECT_GETS`` 登记并写明「为何必须 GET + 如何防 CSRF」。
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCAN_DIRS = (
    "packages/xadmin-common/common",
    "system",
    "settings",
    "captcha",
    "ai",
    "dataset",
    "message",
    "identity",
    "file",
    "task",
    "audit",
    "approval",
    "notifications",
    "devtools",
)

#: 允许的副作用 GET：``(相对路径, 函数名) -> 理由``。当前为空——新增项必须
#: 同时说明「为何不能改 POST」与「Content-Type/自定义头收紧」的防护。
ALLOWED_SIDE_EFFECT_GETS: dict[tuple[str, str], str] = {}

WRITE_ATTRS = {
    "save",
    "delete",
    "create",
    "bulk_create",
    "bulk_update",
    "update_or_create",
    "get_or_create",
    "set_password",
    "publish",
    "send",
    "send_msg",
    "send_mail",
    "apply_async",
    "logout",
    "login",
    "rotate",
    "regenerate",
    "invalidate",
    "incr",
    "decr",
}
SAFE_RECEIVERS = {
    "data",
    "item",
    "result",
    "payload",
    "config",
    "attrs",
    "context",
    "options",
    "flat",
    "updates",
    "mapping",
    "values",
    "headers",
    "kwargs",
    "params",
    "defaults",
    "meta",
    "fields",
    "query",
    "filters",
    "extra",
    "series",
    "row",
    "rows",
    "cards",
    "layout",
    "design",
    "schema",
    "form_data",
    "response",
    "info",
    "body",
    "cache",
}


def _is_side_effect_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute):
        name = func.attr
        receiver = ast.unparse(func.value).split(".")[-1]
    elif isinstance(func, ast.Name):
        name = func.id
        receiver = ""
    else:
        return False
    if name not in WRITE_ATTRS:
        return False
    if receiver in SAFE_RECEIVERS:
        return False
    return True


def _mentions_method(test: ast.AST, methods: set[str]) -> bool:
    text = ast.unparse(test).lower()
    return "method" in text and any(
        f'"{method.lower()}"' in text or f"'{method.lower()}'" in text for method in methods
    )


def _ends_with_return(stmts: list[ast.stmt]) -> bool:
    return bool(stmts) and isinstance(stmts[-1], ast.Return)


def _scan_statements(stmts: list[ast.stmt]) -> list[str]:
    hits: list[str] = []
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            non_get_branch = _mentions_method(stmt.test, {"PUT", "POST", "PATCH", "DELETE"}) and not _mentions_method(
                stmt.test, {"GET"}
            )
            if non_get_branch:
                continue  # 只在非 GET 方法下执行的写操作
            hits.extend(_scan_statements(stmt.body))
            hits.extend(_scan_statements(stmt.orelse))
            if _mentions_method(stmt.test, {"GET"}) and _ends_with_return(stmt.body):
                # `if method == "GET": return ...` 之后只剩非 GET 分支
                break
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and _is_side_effect_call(node):
                hits.append(ast.unparse(node)[:120])
    return hits


def _get_action_methods(node: ast.AST) -> set[str] | None:
    methods: set[str] = set()
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "action":
            continue
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                methods = {str(elt.value).lower() for elt in kw.value.elts if isinstance(elt, ast.Constant)}
    return methods or None


def iter_get_actions():
    for folder in SCAN_DIRS:
        for path in (ROOT / folder).rglob("*.py"):
            if "__pycache__" in str(path) or "/migrations/" in str(path):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                methods = _get_action_methods(node)
                if methods and "get" in methods:
                    yield path.relative_to(ROOT).as_posix(), node


def test_no_unregistered_side_effect_get():
    violations = []
    for rel_path, node in iter_get_actions():
        hits = _scan_statements(node.body)
        if not hits:
            continue
        key = (rel_path, node.name)
        if key in ALLOWED_SIDE_EFFECT_GETS:
            continue
        violations.append(f"{rel_path}::{node.name} -> {hits}")
    assert not violations, (
        "发现未登记的副作用 GET（Cookie 通道可被跨站触发）：把 action 改为 POST，"
        "或在 ALLOWED_SIDE_EFFECT_GETS 登记理由。\n" + "\n".join(violations)
    )


def test_allowlist_entries_still_exist():
    actions = {(rel_path, node.name) for rel_path, node in iter_get_actions()}
    stale = [key for key in ALLOWED_SIDE_EFFECT_GETS if key not in actions]
    assert not stale, f"副作用 GET 白名单存在失效条目（已删除/已改 POST）：{stale}"
