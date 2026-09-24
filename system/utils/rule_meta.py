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

# 规则类型的配置端元数据（choices 接口下发，配置页据此渲染控件与分组）。
#   input          值输入形态：none=运行期按当前用户注入（无需填写）、
#                  text/json/number/datetime/datetimerange/seconds/user/role/dept/menu
#   value_required 是否需要填写 value（input=none 的类型为 False）
#   default_match  选择该类型后建议的匹配符（前端预置，可改）
#   group          配置页分组：all=全部数据、runtime=跟随当前用户、explicit=指定对象、time=时间条件、free=自定义值
RULE_TYPE_META = {
    "value.all": {"input": "none", "value_required": False, "default_match": "all", "group": "all"},
    "value.text": {"input": "text", "value_required": True, "default_match": "exact", "group": "free"},
    "value.json": {"input": "json", "value_required": True, "default_match": "exact", "group": "free"},
    "value.date": {"input": "seconds", "value_required": True, "default_match": "gte", "group": "time"},
    "value.datetime": {"input": "datetime", "value_required": True, "default_match": "gte", "group": "time"},
    "value.datetime.range": {
        "input": "datetimerange",
        "value_required": True,
        "default_match": "range",
        "group": "time",
    },
    "value.user.id": {"input": "none", "value_required": False, "default_match": "exact", "group": "runtime"},
    "value.user.dept.id": {"input": "none", "value_required": False, "default_match": "exact", "group": "runtime"},
    "value.user.dept.ids": {"input": "none", "value_required": False, "default_match": "in", "group": "runtime"},
    "value.leader.dept.ids": {"input": "none", "value_required": False, "default_match": "in", "group": "runtime"},
    "value.leader.user.ids": {"input": "none", "value_required": False, "default_match": "in", "group": "runtime"},
    "value.table.user.ids": {"input": "user", "value_required": True, "default_match": "in", "group": "explicit"},
    "value.table.role.ids": {"input": "role", "value_required": True, "default_match": "in", "group": "explicit"},
    "value.table.dept.ids": {"input": "dept", "value_required": True, "default_match": "in", "group": "explicit"},
    "value.table.menu.ids": {"input": "menu", "value_required": True, "default_match": "in", "group": "explicit"},
    "value.dept.ids": {"input": "dept", "value_required": True, "default_match": "in", "group": "explicit"},
}

# 配置页分组标题（前端按 group 分段展示，新增类型只需在此登记）
RULE_TYPE_GROUP_TEXTS = {
    "all": "全部数据",
    "runtime": "跟随当前用户（运行时按登录人取值）",
    "explicit": "指定对象",
    "time": "时间条件",
    "free": "自定义值（需手动填写，谨慎使用）",
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
