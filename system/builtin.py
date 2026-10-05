#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""内置标签定义 + 幂等同步（标签域，system app）。

- 内置角色（BUILTIN_ROLES / sync_builtin_roles）属 identity 域，在 identity.builtin；
- name 即业务键（Tag.name unique）：内置标签不可经 API 删除（TagViewSet 删除保护），
  颜色/备注可在标签管理页调整——同步只补缺，不覆盖管理员的合法改动。
"""

from common.utils import get_logger

logger = get_logger(__name__)

# 注意：name/remark 必须是纯字符串而非 gettext_lazy——name 是同步的定位键，
# 惰性翻译会随语言环境漂移（migrate 时与查询时解析结果不同 → 重复建签），
# 与 loadjson 种子数据（纯文本）同口径。
BUILTIN_TAGS = [
    {"name": "重点", "color": "#F56C6C", "remark": "内置标签：标记重点用户、文件或审批单"},
    {"name": "待跟进", "color": "#E6A23C", "remark": "内置标签：待跟进事项"},
    {"name": "归档", "color": "#909399", "remark": "内置标签：已归档对象"},
]


def sync_builtin_tags() -> int:
    """内置标签幂等同步，返回实际变更的标签数（0 = 全部已就绪）。

    按名称三态处理：在用 → 补 builtin 标记（颜色/备注以管理员改动为准，不回写）；
    不存在（含被误删）→ 新建。标签无软删除，无需回收站恢复分支。
    """
    from system.models import Tag

    changed = 0
    for spec in BUILTIN_TAGS:
        tag = Tag.objects.filter(name=spec["name"]).first()
        if tag is None:
            Tag.objects.create(name=spec["name"], color=spec["color"], remark=spec["remark"], builtin=True)
            changed += 1
            logger.info("builtin tag created: %s", spec["name"])
            continue
        if not tag.builtin or not tag.color or not tag.remark:
            tag.builtin = True
            tag.color = tag.color or spec["color"]
            tag.remark = tag.remark or spec["remark"]
            tag.save()
            changed += 1
            logger.info("builtin tag synced: %s", spec["name"])
    return changed
