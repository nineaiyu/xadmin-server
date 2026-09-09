# -*- coding: utf-8 -*-
"""数据字典：类型层 code 唯一校验、items 接口与缓存失效、CRUD。"""

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework.test import APIRequestFactory, force_authenticate

from system.models.dict import DataDict
from system.models.user import UserInfo
from system.utils.dict import get_dict_items
from system.views.admin.dict import DataDictViewSet

pytestmark = pytest.mark.django_db


def _make_user():
    return UserInfo.objects.create_superuser(username="dictrunner", password="x")


def _call(action, user, method="get", data=None, pk=None, params=None):
    factory = APIRequestFactory()
    kwargs = {}
    if params:
        factory = APIRequestFactory()
        request = getattr(factory, method)(f"/api/system/dict/{action}", data=params)
    else:
        request = getattr(factory, method)(f"/api/system/dict/{action}", data, format="json")
    if pk:
        kwargs["pk"] = str(pk)
    force_authenticate(request, user=user)
    return DataDictViewSet.as_view({method: action})(request, **kwargs)


def test_type_code_must_be_unique():
    DataDict.objects.create(code="order_status", label="订单状态")
    with pytest.raises(ValidationError):
        DataDict(code="order_status", label="重复编码").full_clean()


def test_items_returns_active_sorted_by_sort():
    parent = DataDict.objects.create(code="order_status", label="订单状态")
    DataDict.objects.create(parent=parent, code="paid", label="已支付", value="paid", sort=2)
    DataDict.objects.create(parent=parent, code="created", label="已创建", value="created", sort=1)
    DataDict.objects.create(parent=parent, code="hidden", label="停用项", value="hidden", is_active=False)
    items = get_dict_items("order_status")
    assert [item["value"] for item in items] == ["created", "paid"]


def test_items_cache_invalidated_on_save():
    parent = DataDict.objects.create(code="city", label="城市")
    assert get_dict_items("city") == []
    DataDict.objects.create(parent=parent, code="bj", label="北京", value="bj")
    # post_save 信号失效缓存后重新读取应包含新项
    items = get_dict_items("city")
    assert [item["label"] for item in items] == ["北京"]


def test_items_cache_is_used():
    cache.clear()
    parent = DataDict.objects.create(code="hot", label="热度")
    DataDict.objects.create(parent=parent, code="high", label="高", value="high")
    assert len(get_dict_items("hot")) == 1
    # 绕过信号直改 DB（模拟缓存生效期间的变化），缓存值应保持
    DataDict.objects.filter(code="high").update(label="超高")
    assert get_dict_items("hot")[0]["label"] == "高"


def test_items_action_requires_code(superuser):
    response = _call("items", superuser, params={})
    assert response.data["code"] != 1000


def test_items_action_returns_results(superuser):
    parent = DataDict.objects.create(code="level", label="等级")
    DataDict.objects.create(parent=parent, code="l1", label="一级", value="1")
    response = _call("items", superuser, params={"code": "level"})
    assert response.data["code"] == 1000
    assert response.data["data"]["results"][0]["value"] == "1"


def test_create_dict_item_via_api(superuser):
    parent = DataDict.objects.create(code="type_api", label="接口类型")
    response = _call(
        "create",
        superuser,
        method="post",
        data={"parent": str(parent.pk), "code": "vip", "label": "VIP", "value": "vip"},
    )
    assert response.data["code"] == 1000
    assert DataDict.objects.filter(code="vip", parent=parent).exists()


def test_duplicate_item_code_rejected(superuser):
    parent = DataDict.objects.create(code="dup_type", label="重复类型")
    DataDict.objects.create(parent=parent, code="same", label="第一项")
    response = _call(
        "create",
        superuser,
        method="post",
        data={"parent": str(parent.pk), "code": "same", "label": "第二项"},
    )
    assert response.data["code"] != 1000


def test_create_type_without_parent(superuser):
    """回归：类型层创建（不带 parent）不被 UniqueTogetherValidator 误判必填。"""
    response = _call("create", superuser, method="post", data={"code": "type_only", "label": "纯类型"})
    assert response.data["code"] == 1000, response.data
    assert DataDict.objects.filter(code="type_only", parent=None).exists()


def test_unique_guard_no_drf_together_validator():
    """守护：DRF 对 (parent, code) 约束（含带 condition）会生成 UniqueTogetherValidator，
    导致类型层创建 parent 被误判必填——serializer 必须覆写 get_unique_together_validators()
    返回空，唯一性由 validate() 显式查重（test_create_type_without_parent /
    test_duplicate_item_code_rejected 锚定行为）。"""
    from rest_framework.validators import UniqueTogetherValidator

    from system.serializers.dict import DataDictSerializer

    together = [
        validator for validator in DataDictSerializer().validators if isinstance(validator, UniqueTogetherValidator)
    ]
    assert together == []


def test_unique_guard_partial_unique_index_in_db():
    """守护：DB 侧保留带 condition 的部分唯一索引（并发下同类型 code 重复直接被库拒绝）。"""
    from django.db import connection

    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, DataDict._meta.db_table)
    partial = [
        item
        for item in constraints.values()
        if item.get("index") and item.get("unique") and set(item.get("columns", [])) == {"parent_id", "code"}
    ]
    assert partial, "DB 层 (parent_id, code) 部分唯一索引缺失"


def test_unique_guard_type_code_partial_index_in_db():
    """守护：类型层 code 全局唯一必须有 DB 部分唯一索引兜底（并发创建竞态
    是 validate() 显式查重覆盖不了的）。"""
    from django.db import connection

    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, DataDict._meta.db_table)
    partial = [
        item
        for item in constraints.values()
        if item.get("index") and item.get("unique") and item.get("columns") == ["code"]
    ]
    assert partial, "DB 层类型层 code 部分唯一索引缺失"


def test_parent_must_be_dict_type(superuser):
    """字典项不能作为父级：parent 只能选类型层，杜绝三级结构。"""
    parent = DataDict.objects.create(code="p_type", label="父类型")
    item = DataDict.objects.create(parent=parent, code="p_item", label="子项")
    response = _call(
        "create",
        superuser,
        method="post",
        data={"parent": str(item.pk), "code": "orphan", "label": "孤儿项"},
    )
    assert response.data["code"] != 1000


def test_parent_cannot_be_self(superuser):
    """类型行不能把自己设为父级（自引用）。"""
    parent = DataDict.objects.create(code="self_type", label="自引用类型")
    response = _call(
        "partial_update",
        superuser,
        method="patch",
        data={"parent": str(parent.pk)},
        pk=parent.pk,
    )
    assert response.data["code"] != 1000


def test_dict_choice_field_dict_driven_and_fallback():
    """守护：DictChoiceField 选项来自字典（bind 时解析，信号失效后生效），未配置时回退 fallback。"""
    from system.serializers.fields import DictChoiceField

    cache.clear()
    parent = DataDict.objects.create(code="order_flag", label="订单标记")
    DataDict.objects.create(parent=parent, code="hot", label="热卖", value="hot", color="#f56c6c")

    field = DictChoiceField(dict_code="order_flag", fallback_choices=[("legacy", "旧")])
    field.bind("order_flag", None)  # 直构字段需显式 bind 才解析（序列化器内自动触发）
    assert field.choices == {"hot": "热卖"}

    DataDict.objects.create(parent=parent, code="cold", label="滞销", value="cold")
    field2 = DictChoiceField(dict_code="order_flag", fallback_choices=[("legacy", "旧")])
    field2.bind("order_flag", None)
    assert set(field2.choices) == {"hot", "cold"}

    fallback = DictChoiceField(
        dict_code="not_exists",
        fallback_choices=[(0, "未知"), (1, "有")],
        value_cast=int,
    )
    fallback.bind("not_exists", None)
    assert fallback.choices == {0: "未知", 1: "有"}


def test_dict_choice_field_merge_fallback_and_color():
    """守护：merge_fallback 模式下字典项与回退项合并（字典优先、回退补缺），
    写入路径仍可能出现回退枚举值的字段（如登录类型）不会因字典只配部分选项而校验失败；
    color 随 choices 解析进 choice_colors，to_representation 与元数据均携带。"""
    from common.drf.metadata import SimpleMetadataWithFilters
    from system.serializers.fields import DictChoiceField

    cache.clear()
    parent = DataDict.objects.create(code="login_type_dict", label="登录类型")
    DataDict.objects.create(parent=parent, code="pwd", label="密码登录", value="0", color="#67c23a")

    field = DictChoiceField(
        dict_code="login_type_dict",
        fallback_choices=[(0, "账号密码"), (9, "未知")],
        value_cast=int,
        merge_fallback=True,
    )
    field.bind("login_type", None)
    # 字典项 0 覆盖回退项 0 的标签；回退项 9 补缺
    assert field.choices == {0: "密码登录", 9: "未知"}
    assert field.choice_colors == {"0": "#67c23a"}
    # 行内数据携带 color（列表 tag 渲染），无颜色项不带该键
    assert field.to_representation(0) == {"value": 0, "label": "密码登录", "color": "#67c23a"}
    assert field.to_representation(9) == {"value": 9, "label": "未知"}

    # 元数据 choices 附带 color（search-columns 协议扩展键）
    info = SimpleMetadataWithFilters().get_field_info(field)
    assert {"value": 0, "label": "密码登录", "color": "#67c23a"} in info["choices"]
    assert {"value": 9, "label": "未知"} in info["choices"]


def test_export_status_dict_integration():
    """下载中心 status 接入字典：未配置时回退模型枚举（默认行为不变）。"""
    from system.models.export import ExportRecord
    from system.serializers.export import ExportRecordSerializer

    cache.clear()
    field = ExportRecordSerializer().fields["status"]
    assert set(field.choices) == set(dict(ExportRecord.Status.choices))

    parent = DataDict.objects.create(code="export_status", label="导出状态")
    DataDict.objects.create(parent=parent, code="success", label="已完成", value="SUCCESS", color="#67c23a")
    field2 = ExportRecordSerializer().fields["status"]
    assert field2.choices == {"SUCCESS": "已完成"}


def test_dict_choice_field_write_path_accepts_enum_values():
    """守护：字典驱动字段（含 merge 回退）的写入校验必须接受整型枚举值。

    历史 bug：bind 手动重建 choice_strings_to_values 时迭代 [(value, label), ...]
    元组列表，key 变成「整个元组的字符串」，字典驱动字段所有写入报 invalid_choice
    （WS 登录 login_type=8 写日志即触发）。修复后 key 必须是 str(value)。
    """
    from system.serializers.log import LoginLogSerializer
    from system.serializers.user import UserSerializer

    cache.clear()
    # login_type：merge 模式，字典未配置时回退整型枚举
    login_serializer = LoginLogSerializer(
        data={"ipaddress": "127.0.0.1", "status": True, "login_type": 8, "channel_name": "x"},
        ignore_field_permission=True,
    )
    assert login_serializer.is_valid(), login_serializer.errors
    assert login_serializer.validated_data["login_type"] == 8

    # gender：非 merge 模式，fallback 整型枚举写入
    gender_field = UserSerializer().fields["gender"]
    assert gender_field.to_internal_value(1) == 1
    assert gender_field.to_internal_value("1") == 1


def test_user_gender_choices_from_dict():
    """用户 gender 下拉真实接入字典：配置 user_gender 字典后 choices 被字典替换
    （value 整型化）；未配置时由 fallback_choices 兜底模型枚举。"""
    from system.serializers.user import UserSerializer

    cache.clear()
    # 测试库不跑 load_init_json：先回退（无 user_gender 字典）→ 建字典后被替换
    field = UserSerializer().fields["gender"]
    assert set(field.choices) == {0, 1, 2}

    parent = DataDict.objects.create(code="user_gender", label="用户性别")
    DataDict.objects.create(parent=parent, code="secret", label="保密", value="9")
    field2 = UserSerializer().fields["gender"]
    assert field2.choices == {9: "保密"}


def test_is_type_filter_narrows_to_dict_types(superuser):
    """is_type 过滤：true 只看类型层，false 只看字典项。"""
    DataDict.objects.create(code="only_type", label="纯类型")
    parent = DataDict.objects.create(code="with_item", label="带子项的类型")
    DataDict.objects.create(parent=parent, code="item", label="字典项")

    types = _call("list", superuser, params={"is_type": "true"})
    assert {row["code"] for row in types.data["data"]["results"]} == {"only_type", "with_item"}
    items = _call("list", superuser, params={"is_type": "false"})
    assert {row["code"] for row in items.data["data"]["results"]} == {"item"}


def test_color_field_renders_as_color_picker():
    """守护：color 字段必须是 ColorField（input_type=color）。

    前端按 input_type 查渲染器注册表，input_type=color 才渲染成颜色选择器；
    模型 CharField 默认推断为 string，会被渲染成普通文本框。
    """
    from common.drf.metadata import SimpleMetadataWithFilters
    from system.serializers.dict import DataDictSerializer

    field = DataDictSerializer().fields["color"]
    assert getattr(field, "input_type", None) == "color"
    # 前端按元数据 type 查渲染器注册表，这里锚定 search-columns 下发的类型
    assert SimpleMetadataWithFilters().get_field_info(field)["type"] == "color"


def test_list_annotates_children_count(superuser):
    parent = DataDict.objects.create(code="count_type", label="计数类型")
    DataDict.objects.create(parent=parent, code="a", label="A")
    DataDict.objects.create(parent=parent, code="b", label="B")
    response = _call("list", superuser)
    rows = {row["code"]: row for row in response.data["data"]["results"]}
    assert rows["count_type"]["children_count"] == 2
    assert rows["a"]["children_count"] == 0


def test_create_by_parent_code_for_import(superuser):
    """导入场景：用字典类型编码定位父级（parent 主键跨环境无意义）。"""
    DataDict.objects.create(code="imp_type", label="导入类型")
    response = _call(
        "create",
        superuser,
        method="post",
        data={"parent_code": "imp_type", "code": "x", "label": "X", "value": "x"},
    )
    assert response.data["code"] == 1000, response.data
    assert DataDict.objects.filter(code="x", parent__code="imp_type").exists()


def test_create_by_unknown_parent_code_rejected(superuser):
    response = _call(
        "create",
        superuser,
        method="post",
        data={"parent_code": "not_exists", "code": "x", "label": "X"},
    )
    assert response.data["code"] != 1000


def test_batch_active_toggles_and_invalidates_cache(superuser):
    cache.clear()
    parent = DataDict.objects.create(code="ba_type", label="批量启停")
    first = DataDict.objects.create(parent=parent, code="a", label="A", value="a")
    second = DataDict.objects.create(parent=parent, code="b", label="B", value="b")
    assert len(get_dict_items("ba_type")) == 2

    response = _call(
        "batch_active",
        superuser,
        method="post",
        data={"pks": [str(first.pk)], "is_active": False},
    )
    assert response.data["code"] == 1000
    first.refresh_from_db()
    assert first.is_active is False
    # 逐个 save 触发信号 → 缓存失效，消费端立即只剩启用项
    assert [item["value"] for item in get_dict_items("ba_type")] == ["b"]

    # is_active 省略时按当前状态取反
    _call("batch_active", superuser, method="post", data={"pks": [str(first.pk)]})
    first.refresh_from_db()
    assert first.is_active is True
    assert {item["value"] for item in get_dict_items("ba_type")} == {"a", "b"}
    assert second.is_active is True


def test_move_swaps_sibling_sort(superuser):
    cache.clear()
    parent = DataDict.objects.create(code="move_type", label="排序类型")
    first = DataDict.objects.create(parent=parent, code="a", label="A", value="a", sort=0)
    second = DataDict.objects.create(parent=parent, code="b", label="B", value="b", sort=1)

    response = _call("move", superuser, method="post", data={"direction": "down"}, pk=first.pk)
    assert response.data["code"] == 1000
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.sort > second.sort
    # update() 绕过信号，缓存由 action 显式失效：消费端顺序同步
    assert [item["value"] for item in get_dict_items("move_type")] == ["b", "a"]

    _call("move", superuser, method="post", data={"direction": "up"}, pk=first.pk)
    assert [item["value"] for item in get_dict_items("move_type")] == ["a", "b"]


def test_move_at_boundary_is_noop(superuser):
    parent = DataDict.objects.create(code="edge_type", label="边界类型")
    first = DataDict.objects.create(parent=parent, code="a", label="A", value="a", sort=0)
    DataDict.objects.create(parent=parent, code="b", label="B", value="b", sort=1)
    response = _call("move", superuser, method="post", data={"direction": "up"}, pk=first.pk)
    assert response.data["code"] == 1000
    first.refresh_from_db()
    assert first.sort == 0


def test_move_rejects_unknown_direction(superuser):
    instance = DataDict.objects.create(code="bad_move", label="非法方向")
    response = _call("move", superuser, method="post", data={"direction": "side"}, pk=instance.pk)
    assert response.data["code"] != 1000


def test_refresh_cache_action_clears_all(superuser):
    cache.clear()
    parent = DataDict.objects.create(code="rc_type", label="刷新缓存")
    DataDict.objects.create(parent=parent, code="a", label="A", value="a")
    assert len(get_dict_items("rc_type")) == 1
    # 绕过信号直改 DB（模拟外部改库），缓存仍是旧值
    DataDict.objects.filter(code="a").update(label="AA")
    assert get_dict_items("rc_type")[0]["label"] == "A"
    response = _call("refresh_cache", superuser, method="post")
    assert response.data["code"] == 1000
    assert get_dict_items("rc_type")[0]["label"] == "AA"


def test_locked_dict_cannot_be_deleted(superuser):
    locked = DataDict.objects.create(code="builtin_type", label="内置类型", is_locked=True)
    # 直接调用视图时 ATOMIC_REQUESTS 会把 400 的回滚标记打到测试事务上，
    # 用 savepoint 隔离，断言才能在错误响应后继续查库
    with transaction.atomic():
        response = _call("destroy", superuser, method="delete", pk=locked.pk)
    assert response.data["code"] != 1000
    assert "is_locked" in response.data
    assert DataDict.objects.filter(pk=locked.pk).exists()


def test_batch_destroy_skips_locked(superuser):
    locked = DataDict.objects.create(code="keep_type", label="内置", is_locked=True)
    normal = DataDict.objects.create(code="drop_type", label="可删")
    response = _call("batch_destroy", superuser, method="post", data=[str(locked.pk), str(normal.pk)])
    assert response.data["code"] == 1000
    assert DataDict.objects.filter(pk=locked.pk).exists()
    assert not DataDict.objects.filter(pk=normal.pk).exists()


def test_locked_dict_code_and_parent_are_readonly(superuser):
    """内置字典被代码按 code 引用（DictChoiceField）：改 code / 换父级等于让引用断链。"""
    locked = DataDict.objects.create(code="builtin_code", label="内置", is_locked=True)
    other = DataDict.objects.create(code="other_type", label="其它类型")
    with transaction.atomic():
        response = _call("partial_update", superuser, method="patch", data={"code": "renamed"}, pk=locked.pk)
    assert response.data["code"] != 1000
    with transaction.atomic():
        response = _call("partial_update", superuser, method="patch", data={"parent": str(other.pk)}, pk=locked.pk)
    assert response.data["code"] != 1000
    locked.refresh_from_db()
    assert locked.code == "builtin_code"
    assert locked.parent_id is None


def test_dict_items_localized_by_language():
    """字典 i18n：en 语言优先 label_en（未维护则回落 label），缓存与语言无关。"""
    from django.utils import translation

    parent = DataDict.objects.create(code="i18n_type", label="状态")
    DataDict.objects.create(parent=parent, code="on", label="启用中", label_en="Enabled", value="on")
    cache.clear()
    with translation.override("zh-hans"):
        assert get_dict_items("i18n_type")[0]["label"] == "启用中"
    with translation.override("en"):
        assert get_dict_items("i18n_type")[0]["label"] == "Enabled"
    # 未维护 label_en 的项：en 环境回落中文 label
    DataDict.objects.create(parent=parent, code="off", label="停用", value="off")
    with translation.override("en"):
        labels = {item["value"]: item["label"] for item in get_dict_items("i18n_type")}
    assert labels == {"on": "Enabled", "off": "停用"}
