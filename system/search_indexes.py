#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""全局搜索的 pg_trgm 索引登记（检索加速，不改检索语义）。

- 检索仍是 `icontains`（LIKE 语义、中文可用、通配符口径不变），本模块只登记
  `system/search.py` 提供者文本字段的 pg_trgm GIN 索引：B-tree 命中不了前缀通配，
  trigram 可以（关键词 ≥2 字符；单字符不产生 trigram，回退顺序扫描）；
- **部署形态无关**：只有 PostgreSQL 会真正建索引（迁移内判 vendor 并 try/except），
  其它后端与「pg_trgm 扩展不可用」的库一律跳过——检索仍然正确，只退回顺序扫描，
  不阻断迁移（warning 可观测）；
- 索引清单与豁免理由登记在 docs/architecture/indexes.md；建索引/回滚在
  system/migrations/0010_*（快照自含），新增检索字段时
  tests/unit/system/test_search_indexes.py 的覆盖守护与漂移守护会提示同步。
"""

from dataclasses import dataclass

# 扩展创建幂等；PG 13+ 的 pg_trgm 属 trusted 扩展，库 owner 一般可自行安装
TRGM_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS pg_trgm"


@dataclass(frozen=True)
class TrigramIndex:
    """单个 trigram 索引：表 + 字段 + 索引名（命名与 indexes.md 的 idx_* 约定一致）。"""

    table: str
    field: str
    name: str


# 检索加速清单：用户/文件/审批单/请假（检索面向使用者且表随业务增长）
SEARCH_TRGM_INDEXES = (
    TrigramIndex("system_userinfo", "username", "idx_userinfo_username_trgm"),
    TrigramIndex("system_userinfo", "nickname", "idx_userinfo_nickname_trgm"),
    TrigramIndex("system_userinfo", "email", "idx_userinfo_email_trgm"),
    TrigramIndex("system_userinfo", "phone", "idx_userinfo_phone_trgm"),
    TrigramIndex("system_uploadfile", "filename", "idx_uploadfile_filename_trgm"),
    TrigramIndex("system_approvalrequest", "path", "idx_approvalrequest_path_trgm"),
    TrigramIndex("system_approvalrequest", "module", "idx_approval_module_trgm"),
    TrigramIndex("system_approvalrequest", "object_pk", "idx_approval_object_pk_trgm"),
    TrigramIndex("system_leave", "reason", "idx_leave_reason_trgm"),
)

# 豁免清单（(表, 字段) → 理由）：覆盖守护要求每个检索字段要么在索引清单、要么在此登记
SEARCH_TRGM_EXEMPT = {
    ("system_deptinfo", "name"): "小表（部门千级以内），顺序扫描成本可忽略",
    ("system_deptinfo", "code"): "小表（部门千级以内），顺序扫描成本可忽略",
    ("system_operationlog", "path"): "写热表（每请求落审计）+ 超管低频检索，维持索引评审既有结论",
    ("system_operationlog", "module"): "写热表（每请求落审计）+ 超管低频检索，维持索引评审既有结论",
    ("system_operationlog", "ipaddress"): "写热表（每请求落审计）+ 超管低频检索，维持索引评审既有结论",
}


def create_index_sql(table: str, field: str, name: str) -> str:
    """建索引 SQL（幂等；只影响 LIKE/ILIKE 的执行计划，不改变匹配语义）。"""
    return f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING gin ({field} gin_trgm_ops)"


def drop_index_sql(name: str) -> str:
    return f"DROP INDEX IF EXISTS {name}"
