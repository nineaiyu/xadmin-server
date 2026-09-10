#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : rule_meta
"""数据权限规则的可读文案与语义说明（配置页与预览页共用）。

面向管理员的中文直述（同 permission_preview 的文案惯例，不入 .po）。

单一来源：规则类型 / 匹配符 / 合并语义三份清单只在本模块维护，
配置端（`ModelLabelField.choices` 下发 hint）与预览端（decode_rule）均引用这里，
避免出现「两份平行清单」在新增类型时不同步（曾出现预览文案回退为原始类型码）。
"""

# 16 种数据权限规则类型 → 过滤语义（ModelLabelField.KeyChoices 全覆盖）
RULE_TYPE_TEXTS = {
    "value.text": "文本值",
    "value.json": "JSON 值",
    "value.all": "全部数据",
    "value.datetime": "指定时间",
    "value.datetime.range": "时间范围",
    "value.date": "相对时间窗口",
    "value.user.id": "目标用户本人",
    "value.user.dept.id": "目标用户所在部门",
    "value.user.dept.ids": "目标用户部门及下级",
    "value.dept.ids": "指定部门及下级",
    "value.leader.dept.ids": "目标用户主管的部门及下级",
    "value.leader.user.ids": "目标用户主管部门的成员",
    "value.table.user.ids": "指定用户",
    "value.table.menu.ids": "指定菜单",
    "value.table.role.ids": "指定角色",
    "value.table.dept.ids": "指定部门",
}

# match lookup → 过滤语义（与 common/core/data_scope.py 编译器支持的匹配符对齐）
MATCH_TEXTS = {
    "exact": "等于",
    "iexact": "忽略大小写等于",
    "contains": "包含",
    "icontains": "忽略大小写包含",
    "in": "属于",
    "gt": "大于",
    "gte": "大于等于",
    "lt": "小于",
    "lte": "小于等于",
    "startswith": "以…开头",
    "istartswith": "忽略大小写以…开头",
    "endswith": "以…结尾",
    "iendswith": "忽略大小写以…结尾",
    "regex": "正则匹配",
    "m2m": "多对多包含任一",
    "m2m_all": "多对多包含全部",
    "all": "不限",
    "ip_in": "IP 段内",
}

MODE_OR_TEXT = "或模式（满足任一规则即可见）"
MODE_AND_TEXT = "且模式（需同时满足全部规则）"

DATA_PERMISSION_SEMANTIC_NOTE = (
    "部门祖先链（含本部门，仅启用部门）与个人授权汇入同一授权池，各授权组的结果做「或」合并（取最宽生效）；"
    "同一授权内多规则按该授权的且/或模式组合（且模式下「全部数据」规则被忽略，或模式下支配整组）；"
    "无任何适用授权时默认不可见；绑定菜单的授权仅在对应菜单上下文生效。"
)
