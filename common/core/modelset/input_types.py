#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""字段输入类型辅助：根据 serializer 字段推断前端渲染用的 input_type。

供元数据 Action（search-columns / search-fields）使用。拆分自 modelset.py。

本模块同时是 ``input_type`` **词表的单一事实源**（稳定公共契约，ADR-083）：
``DECLARED_INPUT_TYPES`` 是平台元数据端点可下发的渲染器类型全集，前端
RePlusPage 渲染器注册表（registry.ts + renderers-*.tsx）与 ``docs/schema/``
两份元数据 Schema 的 ``input_type`` 枚举都以此对账（守护测试
``tests/unit/common/test_metadata_schema.py``：载荷闭包 + Schema 锁步；
client 仓库 vitest：词表 ⇄ 注册表双向覆盖对账）。**扩展流程**：新类型先在
下方词表登记（连同呈现归宿：内置渲染器或回退），再同步 Schema 枚举并在
client 侧补注册表/守护——未经登记的类型经真实载荷闭包测试 fail。
"""

from common.core.serializers import BasePrimaryKeyRelatedField

#: 稳定公共契约（ADR-083）：平台可下发的 input_type 全集（封闭核心，不含 api-* 族）。
#: 构成 = DRF label_lookup 实际可达面（string/field/integer/float/boolean/date/datetime/
#: choice/multiple choice/email/file upload/image upload/list）+ 自定义字段声明面
#: （labeled_choice/labeled_multiple_choice/object_related_field/m2m_related_field/json/
#: phone/color）+ 关联族 _file/_image 组合 + columns 覆写 textarea + search 侧 widget
#: 覆写与合成（select-multiple/datetimerange/select-ordering）+ filter widget 默认面
#: （text/number/select）+ 业务显式声明且走回退透传的 input。DRF 长尾类型（decimal/url/
#: time/duration/regex/slug/nested object 等）未实际下发、前端亦无渲染器，**不预登记**——
#: 出现即被闭包测试拦截，届时按扩展流程有意识处置。
DECLARED_INPUT_TYPES: frozenset[str] = frozenset(
    {
        # DRF label_lookup 实际可达面
        "string",
        "field",
        "integer",
        "float",
        "boolean",
        "date",
        "datetime",
        "choice",
        "multiple choice",
        "email",
        "file upload",
        "image upload",
        "list",
        # 自定义序列化器字段声明面（common/core/fields*.py）
        "labeled_choice",
        "labeled_multiple_choice",
        "object_related_field",
        "m2m_related_field",
        "json",
        "phone",
        "color",
        # 关联族 _file / _image 组合（get_upload_input_type_suffix 合成）
        "object_related_field_file",
        "object_related_field_image",
        "m2m_related_field_file",
        "m2m_related_field_image",
        # columns 覆写（metadata_columns：CharField + textarea 样式）
        "textarea",
        # search-fields 侧：django_filters widget 默认面与覆写/合成（metadata）
        "text",
        "number",
        "select",
        "select-multiple",
        "datetimerange",
        "select-ordering",
        # 业务 filterset 显式声明（demo.Book managers：PkMultipleFilter(input_type="input")），
        # 无内置渲染器，搜索回退渲染器透传 valueType=input（plus-pro 文本输入）
        "input",
    }
)

#: 开放前缀族：业务可扩展面（``api-search-*`` 等）。前端 apiSearch 注册组件 +
#: suggest_url 远程联想（ADR-043）；Schema 侧以 pattern 例外放行，不做封闭枚举。
INPUT_TYPE_PREFIX_FAMILIES: tuple[str, ...] = ("api-",)

#: 无内置渲染器、依赖注册表回退语义呈现的登记类型（declared 的子集）：
#: search/form 回退渲染器透传 valueType（detail 不配置即默认文本展示）。
#: 客户端守护用例据此断言「回退类型不得有内置渲染器、非回退类型必有」。
FALLBACK_RENDERED_INPUT_TYPES: frozenset[str] = frozenset({"email", "input"})


def get_upload_input_type_suffix(value, default):
    if hasattr(value, "child_relation"):
        value = value.child_relation
    try:
        if (
            value.queryset.model._meta.label == "system.UploadFile"
            and isinstance(value, BasePrimaryKeyRelatedField)
            and default in ["object_related_field", "m2m_related_field"]
        ):
            return "_file"
    except Exception:
        # 字段类型探测异常：按非文件字段处理（返回空标记）
        pass
    return ""


def get_format_intput_type(value, default=""):
    input_type_prefix = ""
    input_type = default
    input_type_suffix = get_upload_input_type_suffix(value, default)

    if hasattr(value, "input_type") and value.input_type is not None:
        input_type = value.input_type
    if hasattr(value, "input_type_prefix") and value.input_type_prefix is not None:
        input_type_prefix = f"{value.input_type_prefix}_" if value.input_type_prefix else ""
    if hasattr(value, "input_type_suffix") and value.input_type_suffix is not None:
        input_type_suffix = f"_{value.input_type_suffix}" if value.input_type_suffix else ""
    input_type_str = input_type_prefix + input_type + input_type_suffix
    if input_type_str:
        return input_type_str
    return default
