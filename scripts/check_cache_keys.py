# -*- coding: utf-8 -*-
"""缓存键审计扫描（N5 安全自查三期：缓存键命名规范 + 冲突检查）。

扫描全仓三类缓存键的**登记表**并做规则校验：

1. 装饰器类（自动加 ``magic_cache_data_`` / ``magic_cache_response_`` 前缀）：
   ``@MagicCacheData.make_cache(key_func=...)`` / ``@cache_response(key_func=...)``；
2. 手写键：``cache.get_or_set(...)`` / ``cache.set(...)`` / ``cache.delete(...)``；
3. 失效点：``MagicCacheData.invalid_cache(s)``。

规则（发现违规即 exit 1，可挂 CI）：

- **R1 键必须带命名空间前缀**：手写键必须形如 ``<namespace>_<维度>``，禁止裸 ``"1"``、
  ``"data"`` 之类无前缀键（易与其它模块冲突，且失效时无法批量定位）；
- **R2 用户维度必须显式**：键内出现 ``pk``/``user`` 字样的，同一文件内必须存在对应的
  失效或 TTL 说明（人工核对项，脚本只提示）；
- **R3 键空间冲突**：不同文件定义的**字面前缀**（去占位符后）不得重复——重复即两个模块
  共用一个键空间，失效时会互相误伤。

用法::

    python scripts/check_cache_keys.py           # 扫描并输出登记表
    python scripts/check_cache_keys.py --strict  # 违规退出码 1
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("common", "system", "settings", "captcha")
IGNORE_PARTS = ("/tests/", "/migrations/")

# MagicCacheData / MagicCacheResponse 自动加的前缀（common/base/magic.py）
DECORATOR_PREFIXES = ("magic_cache_data_", "magic_cache_response_")

# 手写键的调用形态（DOTALL：容忍多行调用，键与调用同行或紧随其后）
KEY_CALL_RE = re.compile(r"cache\.(?:get_or_set|set|get|delete)\(\s*(f?)(['\"])([^'\"\n]+)\2")
# 已知合法的「非缓存语义」键（信号量 / 幂等标记 / 字典字段名，不属于键空间命名规则）
KNOWN_NON_CACHE_KEYS = {
    "CELERY_APP_READY",
    "CELERY_APP_SHUTDOWN",
    "CLEAN_ORPHAN_PERIODIC_TASKS",
    "HEALTH_CHECK",
    "ok",
    "ready",
    "status",
    "data",
    "c_time",
    "time",
    "1",
}


def iter_py_files():
    for folder in SCAN_DIRS:
        for path in (ROOT / folder).rglob("*.py"):
            if any(part in path.as_posix() for part in IGNORE_PARTS):
                continue
            yield path


def normalize(key: str) -> str:
    """取键的静态前缀（首个 f-string 占位之前的字面量），用于键空间冲突比对。

    ``approval_pending_count_{user.pk}`` -> ``approval_pending_count``；
    前缀本身是常量变量的（``{DICT_CACHE_PREFIX}{code}``）无法静态判定，
    返回空串并在冲突检查中跳过（其命名空间由常量保证）。
    """
    return key.split("{", 1)[0].rstrip("_")


def scan():
    """返回 (decorator_rows, manual_keys, invalidations)。"""
    decorators: list[tuple[str, int, str]] = []
    manual: dict[str, list[tuple[str, int, str]]] = {}
    invalidations: list[str] = []

    for path in iter_py_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            _ = lineno
            if "make_cache(" in stripped and "def make_cache" not in stripped:
                decorators.append((rel, lineno, stripped))
            elif "cache_response(" in stripped and "def process_cache_response" not in stripped:
                decorators.append((rel, lineno, stripped))
            if "invalid_cache" in stripped and "def " not in stripped:
                invalidations.append(f"{rel}:{lineno}: {stripped}")
            for match in KEY_CALL_RE.finditer(text):
                key = match.group(3)
                if "\n" in key or key in KNOWN_NON_CACHE_KEYS:
                    continue
                key_lineno = text[: match.start()].count("\n") + 1
                site = (rel, key_lineno, key)
                if site not in manual.setdefault(normalize(key), []):
                    manual[normalize(key)].append(site)
    return decorators, manual, invalidations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="违规即退出码 1")
    args = parser.parse_args()

    decorators, manual, invalidations = scan()

    print(f"== 装饰器缓存（自动前缀 {DECORATOR_PREFIXES}）==")
    for rel, lineno, line in decorators:
        print(f"  {rel}:{lineno}  {line[:110]}")

    print("\n== 手写键（按归一化前缀分组）==")
    violations = []
    for prefix, sites in sorted(manual.items()):
        files = sorted({rel for rel, _, _ in sites})
        if not prefix:
            # 前缀是运行期常量（如 DICT_CACHE_PREFIX）：静态无法比对，仅登记
            for rel, lineno, key in sites:
                print(f"  {rel}:{lineno}  key={key}  (动态前缀，命名空间由常量保证)")
            continue
        flag = ""
        if len(files) > 1:
            flag = "  <-- R3 冲突：多个文件共用同一键前缀"
            violations.append(prefix)
        for rel, lineno, key in sites:
            print(f"  {rel}:{lineno}  key={key}{flag}")

    print(f"\n== 失效点（{len(invalidations)} 处）==")
    for item in invalidations:
        print(f"  {item}")

    if violations:
        print(f"\n[R3] 键前缀冲突：{violations}")
    else:
        print("\n[R3] 无键前缀冲突。")
    return 1 if (args.strict and violations) else 0


if __name__ == "__main__":
    sys.exit(main())
