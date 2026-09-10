#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""数据字典驱动的序列化器字段：下拉 choices 从 DataDict 取值。

框架层默认下拉由模型 TextChoices/IntegerChoices 写死，改选项要发版；
DictChoiceField 把选项来源切到数据字典（system/utils/dict.py 缓存 + 变更信号
失效），管理员在字典页增删选项/改标签即时生效。字典未配置或为空时回退
fallback_choices，业务不因缺字典数据而中断。

choices 解析分两步：__init__ 解析一次（供直构/Schema 场景），bind() 按请求
重新解析——DRF 的 declared field 在类定义时实例化、每次序列化器实例化只做
deepcopy，不重跑 __init__，因此选项刷新必须挂在 bind 上。
"""

from common.core.fields import LabeledChoiceField
from system.utils.dict import get_dict_items


class DictChoiceField(LabeledChoiceField):
    """选项来自数据字典的 ChoiceField（继承 LabeledChoiceField 保持 {value,label} 契约）。

    :param dict_code: 字典类型 code（parent 为空的类型行）
    :param fallback_choices: 字典未配置/为空时回退的 choices（通常传模型 Choices.choices）
    :param value_cast: 字典项 value 的类型回调（如 int），适配整型枚举模型字段
    :param merge_fallback: True 时字典项与回退项合并（字典项优先，回退项补缺）——
        适用于写入路径仍可能出现回退枚举值的字段（如登录类型），避免字典只配了
        部分选项导致其余合法值校验失败；默认 False（字典项完全替换）
    """

    def __init__(self, *, dict_code, fallback_choices=None, value_cast=None, merge_fallback=False, **kwargs):
        kwargs.pop("choices", None)
        self.dict_code = dict_code
        self.fallback_choices = list(fallback_choices) if fallback_choices else []
        self.value_cast = value_cast
        self.merge_fallback = merge_fallback
        # 字典项颜色映射（value -> color），bind 时随选项一起解析；
        # 元数据（common/drf/metadata.py）据此把 color 注入 choices，前端渲染 tag
        self.choice_colors = {}
        # import 期不查库（declared field 在类定义时实例化，查库会让任意 import
        # user 序列化器的模块在无 DB 上下文中崩溃）：先用回退项占位，bind() 再解析
        super().__init__(choices=self.fallback_choices, **kwargs)

    def _cast(self, value):
        return value if self.value_cast is None else self.value_cast(value)

    def resolve_choices(self):
        """读字典当前项（get_dict_items 带缓存，信号变更后失效重读）；空则回退。"""
        items = get_dict_items(self.dict_code)
        choices = [(self._cast(item["value"]), item["label"]) for item in items if item["value"] is not None]
        self.choice_colors = {
            str(self._cast(item["value"])): item["color"] for item in items if item["value"] is not None
        }
        if self.merge_fallback:
            seen = {str(key) for key, _ in choices}
            merged = list(choices) + [(key, label) for key, label in self.fallback_choices if str(key) not in seen]
            return merged
        return choices if choices else self.fallback_choices

    def bind(self, field_name, parent):
        super().bind(field_name, parent)
        # 字段实例来自类级 declared field 的 deepcopy：绑定到具体序列化器时
        # 重新解析字典，保证同进程内字典变更（信号失效缓存）对后续请求生效。
        # 注意：绝不能再手动重建 choice_strings_to_values——resolve_choices 返回
        # [(value, label), ...] 元组列表，直接迭代会把「整个元组的字符串」当 key
        # （历史 bug：字典驱动字段全部写入报 invalid_choice）；DRF 的 choices
        # setter 内部已用 flatten 后的 dict 正确重建该映射。
        self.choices = self.resolve_choices()

    def to_representation(self, key):
        if key is None:
            return key
        # fallback 枚举的 label 是 gettext_lazy 代理：必须物化为 str，
        # 否则 celery worker 里站内信 WS 推送（msgpack）抛 can not serialize '__proxy__'
        data = {"value": key, "label": str(self.choices.get(key, key))}
        color = self.choice_colors.get(str(key))
        if color:
            data["color"] = color
        return data
