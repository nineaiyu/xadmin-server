# -*- coding: utf-8 -*-
"""system/services/modelfield.py：字段 lookup 说明与模型字段同步。"""

import pytest
from django.conf import settings
from rest_framework.test import APIRequestFactory, force_authenticate

from system.services.modelfield import get_extra_field_lookups, get_field_lookup_info
from system.views.admin.modelfield import ModelLabelFieldViewSet


def test_lookup_info_covers_known_lookups():
    fields = ["exact", "icontains", "in", "gt", "isnull"]
    result = get_field_lookup_info(fields)
    assert [r["value"] for r in result] == fields
    for item in result:
        assert item["label"]  # 已知 lookup 必须有翻译说明


def test_framework_special_lookups_have_labels():
    """框架自定义匹配符（m2m/m2m_all/ip_in）必须有中文/可读说明，供前端 match 下拉展示。"""
    result = get_field_lookup_info(["m2m", "m2m_all", "ip_in"])
    assert [r["value"] for r in result] == ["m2m", "m2m_all", "ip_in"]
    for item in result:
        assert item["label"] != item["value"]  # 不得回退为裸 lookup 名


@pytest.mark.django_db
def test_extra_lookups_by_field_type():
    """自定义匹配符按字段类型暴露：多对多给 m2m/m2m_all，IP 字段给 ip_in，普通字段不给。"""
    from audit.models import UserLoginLog
    from identity.models import UserInfo

    m2m_field = UserInfo._meta.get_field("roles")
    assert get_extra_field_lookups(m2m_field) == ["m2m", "m2m_all"]

    ip_field = UserLoginLog._meta.get_field("ipaddress")
    assert get_extra_field_lookups(ip_field) == ["ip_in"]

    char_field = UserInfo._meta.get_field("username")
    assert get_extra_field_lookups(char_field) == []


def test_lookup_info_unknown_lookup_falls_back_to_name():
    result = get_field_lookup_info(["custom_lookup"])
    assert result == [{"value": "custom_lookup", "label": "custom_lookup"}]


def test_lookup_info_empty_fields():
    assert get_field_lookup_info([]) == []


@pytest.mark.django_db
class TestSyncModelField:
    def test_sync_model_field_creates_label_fields(self):
        from system.models import ModelLabelField
        from system.services.modelfield import sync_model_field

        sync_model_field()
        # 数据权限维度：所有表/所有字段节点存在
        assert ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.DATA, parent=None).exists()
        # 角色字段权限维度：按序列化器生成
        assert ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.ROLE).exists()

        # 幂等：重复执行不报错且数量收敛（旧记录被清理）
        count = ModelLabelField.objects.count()
        sync_model_field()
        assert ModelLabelField.objects.count() <= count

    def test_get_app_model_fields_includes_system_models(self):
        from system.models import ModelLabelField
        from system.services.modelfield import get_app_model_fields

        get_app_model_fields()
        names = set(
            ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.DATA).values_list("name", flat=True)
        )
        assert "*" in names
        assert any(name.startswith("system.") for name in names)

    def test_prune_removes_stale_rows_with_null_updated_time(self):
        """清理陈旧行不得依赖 updated_time（种子 loaddata 写入的行该列为 NULL）。"""
        from system.models import ModelLabelField
        from system.services.modelfield import sync_model_field

        sync_model_field()
        stale = ModelLabelField.objects.create(
            name="stale.field.for.test", label="stale", field_type=ModelLabelField.FieldChoices.ROLE
        )
        ModelLabelField.objects.filter(pk=stale.pk).update(updated_time=None)
        assert ModelLabelField.objects.filter(pk=stale.pk).exists()

        sync_model_field()
        assert not ModelLabelField.objects.filter(pk=stale.pk).exists()

    def test_role_tree_covers_key_models(self):
        """ROLE 字段树必须覆盖已导入序列化器的模型节点（URLconf 预热，规模不得退化）。

        历史缺陷：管理命令环境下 views 模块未导入，``BaseModelSerializer.__subclasses__()``
        只能收集到少量子类（实测 36 vs 765 条），角色页无法为缺失字段配置权限白名单，
        且种子回写会把缺项固化。
        """
        from system.models import ModelLabelField
        from system.services.modelfield import sync_model_field

        sync_model_field()
        role_qs = ModelLabelField.objects.filter(field_type=ModelLabelField.FieldChoices.ROLE)
        assert role_qs.count() > 300, f"ROLE 字段树规模异常（{role_qs.count()}），检查 URLconf 预热"
        covered = set(role_qs.filter(parent=None).values_list("name", flat=True))
        for model_label in ("identity.userinfo", "system.menu", "identity.userrole", "system.datadict"):
            assert model_label in covered, f"ROLE 字段树缺模型节点：{model_label}"

    def test_broken_serializer_is_skipped_not_fatal(self):
        """单个序列化器实例化异常只跳过并登记，不中断全量同步。"""
        from common.core.serializers import BaseModelSerializer
        from system.services.modelfield import sync_model_field

        class BrokenProbeSerializer(BaseModelSerializer):
            def __init__(self, *args, **kwargs):
                raise ValueError("broken probe")

        result = sync_model_field()
        assert "BrokenProbeSerializer" in result["role"]["failed_serializers"]
        assert result["role"]["kept"] > 0

    def test_seed_matches_sync_scope(self, superuser, seed_creator_user):
        """种子字段树不得在同步时被判为陈旧删除（单向守护）。

        失败模式：app 拆分批次（如 system → approval/ai）只改种子里的模型前缀、
        忘改扫描范围设置，则种子行在首次同步时被当作陈旧行静默删除——用户侧表现为
        「同步后字段数掉了好几百」，且规则选择器再也看不到这些模型。

        只断言「种子行被删」方向：反向（同步产出多于种子）在测试进程里必然发生
        ——`BaseModelSerializer.__subclasses__()` 会收集同进程其它测试模块声明的
        探针序列化器，且这些行会在同步时补进库，属预期噪声。

        只 loaddata 单文件 fixture，**不走 load_init_json**：该命令会把
        ``ModelSignal.send`` 全局改写成忽略信号的实现（进程级副作用、不还原，
        见 test_module_seed.py 的既有约定），在测试进程里调用会污染后续所有
        依赖信号失效的用例。
        """
        import json
        from pathlib import Path

        from django.core.management import call_command

        from system.models import ModelLabelField
        from system.services.modelfield import sync_model_field

        seed_path = Path(settings.PROJECT_DIR) / "loadjson" / "modellabelfield.json"
        seed_rows = json.loads(seed_path.read_text(encoding="utf-8"))
        seed_pks = {row["pk"] for row in seed_rows}

        # 种子内容自身守护（静态、与运行进程无关）：拆分自 system 的 app 必须留在
        # 数据权限表树里，否则规则选择器看不到这些模型（历史回归即发生在此）
        roots = {
            row["fields"]["name"]
            for row in seed_rows
            if row["fields"]["field_type"] == 1 and not row["fields"]["parent"]
        }
        for model_label in ("approval.approvalinstance", "ai.aiprofile", "identity.post"):
            assert model_label in roots, f"数据权限表树种子缺模型：{model_label}"

        # 种子行 creator=1 为约定值：测试库首个用户即 pk=1（superuser fixture）
        call_command("loaddata", str(seed_path), verbosity=0)
        sync_model_field()

        live_pks = {str(pk) for pk in ModelLabelField.objects.values_list("pk", flat=True)}
        removed = seed_pks - live_pks
        assert not removed, (
            f"{len(removed)} 行种子字段节点在同步后被判为陈旧（扫描范围与种子不一致，重新导出前先查 PERMISSION_DATA_AUTH_APPS）"
        )


@pytest.mark.django_db
class TestSyncEndpoint:
    """sync 接口的方法契约：全量字段同步有写副作用，只暴露 POST，GET 返回 405。"""

    @staticmethod
    def _dispatch(method, user):
        factory = APIRequestFactory()
        request = getattr(factory, method)("/api/system/field/sync")
        force_authenticate(request, user=user)
        # 与路由注册同口径：sync 仅注册 POST，GET 落到 http_method_not_allowed
        view = ModelLabelFieldViewSet.as_view({"post": "sync"})
        return view(request)

    def test_get_is_not_allowed(self, superuser):
        # GET 会被浏览器预取/爬虫/代理重放误触发，写副作用端点必须拒绝
        response = self._dispatch("get", superuser)
        assert response.status_code == 405

    def test_post_returns_sync_summary(self, superuser):
        response = self._dispatch("post", superuser)
        assert response.status_code == 200
        assert response.data["code"] == 1000
        # 响应结构与改前一致：data 携带数据权限树 / 角色字段树两维同步摘要
        summary = response.data["data"]
        assert set(summary) == {"data", "role"}
        assert set(summary["data"]) == {"kept", "deleted", "models"}
        assert summary["role"]["kept"] > 0
        assert "failed_serializers" in summary["role"]
