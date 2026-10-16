# -*- coding: utf-8 -*-
"""跨域切分回灌（legacy_export / legacy_import）守护：导出包格式、往返、幂等与引用缺失。"""

import json
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command

from system.utils.platform import legacy_transfer as lt


def _patch_export_models(monkeypatch):
    """把导出侧模型指向当前模型，让单库内的往返测试可跑（真实场景中导出在旧代码执行）。"""
    for name in lt.entity_names():
        monkeypatch.setitem(lt.ENTITIES[name], "export_model", lt.ENTITIES[name]["import_model"])


def _seed():
    """构造一批业务数据，返回关键对象。"""
    from file.models import UploadFile
    from identity.models import DeptInfo, UserInfo, UserRole
    from system.models import DataPermission, Menu, MenuMeta, SystemConfig

    meta = MenuMeta.objects.create(title="用户列表")
    menu = Menu.objects.create(name="system.userinfo.list", path="/api/identity/userinfo", menu_type=1, meta=meta)
    dept = DeptInfo.objects.create(name="研发中心", code="D100", rank=1)
    child = DeptInfo.objects.create(name="前端组", code="D110", parent=dept, rank=2)
    role = UserRole.objects.create(name="研发角色", code="R_DRILL_DEV")
    role.menu.set([menu])
    rule = DataPermission.objects.create(name="演练-仅本部门", rules=[{"field": "dept", "value": "self"}])
    rule.menu.set([menu])
    user = UserInfo.objects.create_user(username="drill_user_a", password="Drill@2026!")
    user.nickname = "张开发"
    user.dept = child
    user.email = "drill_a@drill.local"
    user.save()
    user.roles.set([role])
    user.rules.set([rule])
    SystemConfig.objects.create(key="DRILL_BANNER", value={"text": "演练", "enabled": True})
    upload = UploadFile(filename="drill-note.txt", filesize=5, mime_type="text/plain", is_upload=True)
    upload.filepath.save("drill-note.txt", ContentFile(b"hello"), save=True)
    return {"dept": dept, "child": child, "role": role, "rule": rule, "user": user, "upload": upload}


class TestSerialization:
    def test_serialize_value_covers_db_types(self):
        import datetime
        import decimal
        import uuid

        assert lt.serialize_value(None) is None
        assert lt.serialize_value(1) == 1
        assert lt.serialize_value(uuid.UUID(int=1)) == "00000000-0000-0000-0000-000000000001"
        assert lt.serialize_value(datetime.date(2026, 10, 17)) == "2026-10-17"
        assert lt.serialize_value(decimal.Decimal("1.50")) == "1.50"
        assert lt.serialize_value({"a": uuid.UUID(int=1)}) == {"a": "00000000-0000-0000-0000-000000000001"}

    def test_bytes_roundtrip(self):
        payload = lt.serialize_value(b"abc")
        assert lt.deserialize_value(payload) == b"abc"

    def test_parse_include_rejects_unknown_entity(self):
        with pytest.raises(ValueError):
            lt.parse_include("users,not_an_entity")
        assert lt.parse_include("users").pop() == "users"
        assert "depts" in lt.parse_include(None)

    def test_validate_payload_rejects_foreign_and_newer(self):
        with pytest.raises(ValueError):
            lt.validate_payload({"format": "other", "format_version": 1, "data": {}})
        with pytest.raises(ValueError):
            lt.validate_payload({"format": lt.FORMAT, "format_version": lt.FORMAT_VERSION + 1, "data": {}})
        lt.validate_payload(lt.build_payload("4.2.5", {"depts": []}))


@pytest.mark.django_db
class TestRoundTrip:
    def _export(self, target, monkeypatch, extra_args=()):
        _patch_export_models(monkeypatch)
        call_command("legacy_export", "--output", str(target), *extra_args, stdout=StringIO())
        return json.loads((target / "export.json").read_text(encoding="utf-8"))

    def test_export_covers_entities_and_natural_keys(self, tmp_path, monkeypatch):
        _seed()
        payload = self._export(tmp_path / "legacy", monkeypatch)
        assert payload["format"] == lt.FORMAT
        assert payload["counts"]["depts"] == 2
        assert payload["counts"]["users"] >= 1
        assert payload["counts"]["files"] == 1

        dept_rows = {row["code"]: row for row in payload["data"]["depts"]}
        assert dept_rows["D110"]["parent"] == "D100"

        user_row = next(row for row in payload["data"]["users"] if row["username"] == "drill_user_a")
        # 关联关系一律落成自然键（旧库 UUID 不参与回灌）
        assert user_row["dept"] == "D110"
        assert user_row["roles"] == ["R_DRILL_DEV"]
        assert user_row["rules"] == ["演练-仅本部门"]
        assert "$" in user_row["password"]  # 密码哈希原样搬运（测试档 hasher 与生产不同）

        role_row = next(row for row in payload["data"]["roles"] if row["code"] == "R_DRILL_DEV")
        assert role_row["menu"] == ["system.userinfo.list"]

    def test_import_restores_modified_data(self, tmp_path, monkeypatch):
        from file.models import UploadFile
        from identity.models import DeptInfo, UserInfo, UserRole

        seeded = _seed()
        payload = self._export(tmp_path / "legacy", monkeypatch)
        assert payload["counts"]["users"] >= 1

        # 模拟"清库重建后的新库"：业务数据被改动
        UserInfo.objects.filter(username="drill_user_a").update(nickname="被改过的昵称")
        UserInfo.objects.get(username="drill_user_a").roles.clear()
        UserRole.objects.filter(code="R_DRILL_DEV").delete()  # 软删：回灌应能恢复
        DeptInfo.objects.filter(code="D110").update(name="被改过的部门名", parent=None)

        call_command("legacy_import", "--input", str(tmp_path / "legacy"), stdout=StringIO())

        user = UserInfo.objects.get(username="drill_user_a")
        assert user.nickname == "张开发"
        assert user.dept is not None and user.dept.code == "D110"
        assert list(user.roles.values_list("code", flat=True)) == ["R_DRILL_DEV"]
        child = DeptInfo.objects.get(code="D110")
        assert child.name == "前端组"
        assert child.parent is not None and child.parent.code == "D100"
        assert UploadFile.objects.filter(filename=seeded["upload"].filename).exists()

    def test_import_is_idempotent(self, tmp_path, monkeypatch):
        from identity.models import DeptInfo, UserInfo, UserRole

        _seed()
        self._export(tmp_path / "legacy", monkeypatch)
        call_command("legacy_import", "--input", str(tmp_path / "legacy"), stdout=StringIO())

        before = (DeptInfo.objects.count(), UserRole.objects.count(), UserInfo.objects.count())
        call_command("legacy_import", "--input", str(tmp_path / "legacy"), stdout=StringIO())
        after = (DeptInfo.objects.count(), UserRole.objects.count(), UserInfo.objects.count())
        assert before == after

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        from identity.models import DeptInfo

        _seed()
        self._export(tmp_path / "legacy", monkeypatch)
        DeptInfo.objects.filter(code="D110").update(name="被改过的部门名")

        call_command("legacy_import", "--input", str(tmp_path / "legacy"), "--dry-run", stdout=StringIO())
        assert DeptInfo.objects.get(code="D110").name == "被改过的部门名"

    def test_missing_reference_is_reported_not_fatal(self, tmp_path, monkeypatch):
        from identity.models import UserRole
        from system.models import Menu

        _seed()
        payload = self._export(tmp_path / "legacy", monkeypatch)
        # 目标库缺少该权限点（例如种子未灌 / 已被删除）：导入应跳过并汇总告警，而不是中断
        Menu.objects.filter(name="system.userinfo.list").delete()
        assert not Menu.objects.filter(name="system.userinfo.list").exists()

        out = StringIO()
        call_command("legacy_import", "--input", str(tmp_path / "legacy"), stdout=out)
        rendered = out.getvalue()
        assert "引用缺失" in rendered
        assert "roles.menu=system.userinfo.list" in rendered
        assert UserRole.objects.filter(code="R_DRILL_DEV").exists()

        assert any(row["code"] == "R_DRILL_DEV" for row in payload["data"]["roles"])

    def test_notice_attachment_is_linked(self, tmp_path, monkeypatch):
        """公告的附件关联（notice.file）也要回灌。"""
        from file.models import UploadFile
        from notifications.models.message import MessageContent

        _seed()
        upload = UploadFile.objects.first()
        notice = MessageContent.objects.create(title="带附件公告", message="正文", creator=None)
        notice.file.set([upload])
        exports = self._export(tmp_path / "legacy", monkeypatch)
        assert exports["counts"]["notices"] >= 1
        row = next(item for item in exports["data"]["notices"] if item["title"] == "带附件公告")
        assert row["file"] == [upload.filename]

        MessageContent.objects.filter(title="带附件公告").delete()
        call_command("legacy_import", "--input", str(tmp_path / "legacy"), stdout=StringIO())
        restored = MessageContent.objects.get(title="带附件公告")
        assert list(restored.file.values_list("filename", flat=True)) == [upload.filename]

    def test_missing_attachment_file_is_skipped(self, tmp_path, monkeypatch):
        """附件记录在、物理文件不在：跳过该记录（不建下载不了的坏行），不中断导入。"""
        from file.models import UploadFile

        _seed()
        self._export(tmp_path / "legacy", monkeypatch)
        for path in (tmp_path / "legacy" / lt.FILES_DIR).rglob("*"):
            if path.is_file():
                path.unlink()
        # 软删会留下同名记录（回灌走「更新」分支），这里物理删除以模拟全新库
        for item in UploadFile.objects.all():
            item.hard_delete()

        out = StringIO()
        call_command("legacy_import", "--input", str(tmp_path / "legacy"), stdout=out)
        assert "files.missing" in out.getvalue()
        assert not UploadFile.objects.filter(filename="drill-note.txt").exists()

    def test_entity_failure_does_not_block_others(self, tmp_path, monkeypatch):
        """单实体失败只跳过该实体：后续实体与收尾（部门父子链接）仍须执行。"""
        from identity.models import DeptInfo
        from system.management.commands import legacy_import as module

        _seed()
        self._export(tmp_path / "legacy", monkeypatch)
        DeptInfo.objects.filter(code="D110").update(name="被改过的部门名", parent=None)

        def boom(self, rows):
            raise RuntimeError("模拟公告导入失败")

        monkeypatch.setattr(module.Importer, "_import_notices", boom)
        out, err = StringIO(), StringIO()
        call_command("legacy_import", "--input", str(tmp_path / "legacy"), stdout=out, stderr=err)
        assert "导入失败" in err.getvalue()
        child = DeptInfo.objects.get(code="D110")
        assert child.name == "前端组"
        assert child.parent is not None and child.parent.code == "D100"

    def test_personal_config_and_user_avatar_roundtrip(self, tmp_path, monkeypatch):
        """用户个人配置与头像（模型自带文件字段）也应跨库搬运。"""
        from identity.models import UserInfo
        from system.models import UserPersonalConfig

        _seed()
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c6300010000050001a5f645400000000049454e44ae426082"
        )
        user = UserInfo.objects.get(username="drill_user_a")
        user.avatar.save("avatar.png", ContentFile(png), save=True)
        UserPersonalConfig.objects.create(owner=user, key="table-size", value={"size": "small"})

        payload = self._export(tmp_path / "legacy", monkeypatch)
        assert payload["counts"]["personal_configs"] == 1
        user_row = next(row for row in payload["data"]["users"] if row["username"] == "drill_user_a")
        assert user_row["avatar_path"].endswith(".png")  # upload_to 会重命名，只校验类型

        UserPersonalConfig.objects.all().delete()
        UserInfo.objects.filter(username="drill_user_a").update(avatar="")

        call_command("legacy_import", "--input", str(tmp_path / "legacy"), stdout=StringIO())
        restored = UserInfo.objects.get(username="drill_user_a")
        assert restored.avatar and restored.avatar.name.endswith(".png")
        assert UserPersonalConfig.objects.filter(owner=restored, key="table-size").exists()

    def test_settings_and_subscriptions_roundtrip(self, tmp_path, monkeypatch):
        """系统设置、用户/系统消息订阅也要回灌；加密设置的取值跨库不可解，只搬元信息并告警。"""
        from identity.models import UserInfo
        from notifications.models.notification import SystemMsgSubscription, UserMsgSubscription
        from settings.models import Setting

        _seed()
        Setting.objects.create(name="drill.plain", value='{"a": 1}', category="drill")
        Setting.objects.create(name="drill.secret", value="Salted__ciphertext", category="drill", encrypted=True)
        user = UserInfo.objects.get(username="drill_user_a")
        UserMsgSubscription.objects.create(user=user, message_type="drill.notice", receive_backends=["system"])
        system_sub = SystemMsgSubscription.objects.create(
            message_type="drill.system.notice", receive_backends=["email"]
        )
        system_sub.users.set([user])

        payload = self._export(tmp_path / "legacy", monkeypatch)
        assert payload["counts"]["settings"] >= 2
        assert payload["counts"]["user_msg_subscriptions"] >= 1
        assert payload["counts"]["system_msg_subscriptions"] >= 1

        Setting.objects.filter(name__startswith="drill.").delete()
        UserMsgSubscription.objects.filter(message_type="drill.notice").delete()
        SystemMsgSubscription.objects.filter(message_type="drill.system.notice").delete()

        out = StringIO()
        call_command("legacy_import", "--input", str(tmp_path / "legacy"), stdout=out)
        assert Setting.objects.get(name="drill.plain").value == '{"a": 1}'
        secret = Setting.objects.get(name="drill.secret")
        assert secret.encrypted is True
        assert not secret.value, "加密取值不得原样搬运（旧库密钥签发的密文在新库解不开）"
        assert "加密设置" in out.getvalue()
        assert UserMsgSubscription.objects.filter(user=user, message_type="drill.notice").exists()
        restored = SystemMsgSubscription.objects.get(message_type="drill.system.notice")
        assert list(restored.users.values_list("username", flat=True)) == [user.username]

    def test_field_permission_roundtrip(self, tmp_path, monkeypatch):
        """字段权限：角色 × 菜单 + 字段树引用（复合自然键 名称|类型）。"""
        from identity.models import UserRole
        from system.models import FieldPermission, Menu, ModelLabelField

        _seed()
        menu = Menu.objects.get(name="system.userinfo.list")
        role = UserRole.objects.get(code="R_DRILL_DEV")
        field = ModelLabelField.objects.create(name="userinfo", label="用户", field_type=1)
        permission = FieldPermission.objects.create(role=role, menu=menu)
        permission.field.set([field])

        payload = self._export(tmp_path / "legacy", monkeypatch)
        assert payload["counts"]["field_permissions"] >= 1
        row = next(item for item in payload["data"]["field_permissions"] if item["menu"] == menu.name)
        assert row["field"] == ["userinfo|1"], "字段树应按 名称|类型 复合键导出"

        FieldPermission.objects.filter(role=role, menu=menu).delete()
        call_command("legacy_import", "--input", str(tmp_path / "legacy"), stdout=StringIO())
        restored = FieldPermission.objects.get(role=role, menu=menu)
        assert list(restored.field.values_list("name", flat=True)) == ["userinfo"]

    def test_primary_keys_are_preserved_and_sequence_reset(self, tmp_path, monkeypatch):
        """回灌沿用旧库 ID（二开表外键引用才有效），并把自增序列推到最大值。"""
        from identity.models import DeptInfo, UserInfo, UserRole

        _seed()
        user = UserInfo.objects.get(username="drill_user_a")
        dept = DeptInfo.objects.get(code="D110")
        role = UserRole.objects.get(code="R_DRILL_DEV")
        old_ids = (user.pk, dept.pk, role.pk)
        self._export(tmp_path / "legacy", monkeypatch)

        for item in UserInfo.objects.filter(username="drill_user_a"):
            item.hard_delete()
        for item in UserRole.objects.filter(code="R_DRILL_DEV"):
            item.hard_delete()
        DeptInfo.objects.filter(code="D110").delete()  # DeptInfo 无软删，直接物理删除

        call_command("legacy_import", "--input", str(tmp_path / "legacy"), stdout=StringIO())

        assert UserInfo.objects.get(username="drill_user_a").pk == old_ids[0]
        assert DeptInfo.objects.get(code="D110").pk == old_ids[1]
        assert UserRole.objects.get(code="R_DRILL_DEV").pk == old_ids[2]
        # 序列已推进：新建记录不会撞主键（未重置时这里会 IntegrityError）
        fresh = UserInfo.objects.create_user(username="after_import_user", password="After@2026!")
        assert int(fresh.pk) > int(old_ids[0])

    def test_extra_table_without_model_is_reported(self, tmp_path):
        """二开表在目标库没有对应模型时只告警跳过，不中断导入。"""
        from system.utils.platform.legacy_importer import Importer

        importer = Importer(tmp_path)
        results = importer.import_extra_tables({"not_a_real_table": [{"id": 1}]})
        assert results == [("not_a_real_table", 0, 1)]
        assert any(key.startswith("extra_table=") for key in importer.skipped_refs)

    def test_discover_extra_tables_filters_framework_tables(self):
        """自动识别二开表：框架前缀与已映射的表都要排除。"""
        module = _load_standalone()

        class FakeCursor:
            def execute(self, *args, **kwargs):
                return None

            def fetchall(self):
                return [
                    ("system_userinfo",),
                    ("django_migrations",),
                    ("celery_taskmeta",),
                    ("myapp_thing",),
                    ("myapp_other",),
                ]

        assert sorted(module.discover_extra_tables(FakeCursor())) == ["myapp_other", "myapp_thing"]

    def test_unknown_include_is_command_error(self, tmp_path):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError):
            call_command("legacy_export", "--output", str(tmp_path), "--include", "nope", stdout=StringIO())
        with pytest.raises(CommandError):
            call_command("legacy_import", "--input", str(tmp_path), "--include", "nope", stdout=StringIO())


def _load_standalone():
    """按路径加载 ops/legacy_export.py（独立脚本，不在包内）。"""
    import importlib.util
    from pathlib import Path

    from django.conf import settings

    path = Path(settings.BASE_DIR) / "ops" / "legacy_export.py"
    spec = importlib.util.spec_from_file_location("legacy_export_standalone", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _test_dsn():
    from django.conf import settings

    db = settings.DATABASES["default"]
    return f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"


@pytest.mark.django_db(transaction=True)
class TestStandaloneExport:
    """独立导出脚本（旧库侧）：表名映射、自然键关联与多对多导出。"""

    @staticmethod
    def _map_to_current_schema(monkeypatch, module):
        """把脚本的表名映射指向当前结构（脚本默认面向四域切分前的旧表名）。"""
        monkeypatch.setitem(module.TABLE_MAP, "depts", ("identity_deptinfo", "code"))
        monkeypatch.setitem(module.TABLE_MAP, "roles", ("identity_userrole", "code"))
        monkeypatch.setitem(module.TABLE_MAP, "users", ("identity_userinfo", "username"))
        monkeypatch.setitem(module.REFERENCE_TABLES, "menus", ("system_menu", "name"))
        monkeypatch.setattr(
            module,
            "M2M_MAP",
            (
                ("depts", "roles", "identity_deptinfo_roles", "deptinfo_id", "userrole_id", "roles"),
                ("roles", "menu", "identity_userrole_menu", "userrole_id", "menu_id", "menus"),
                ("users", "roles", "identity_userinfo_roles", "userinfo_id", "userrole_id", "roles"),
            ),
        )

    def test_export_keeps_relations_and_m2m(self, tmp_path, monkeypatch):
        from identity.models import DeptInfo, UserInfo, UserRole

        module = _load_standalone()
        self._map_to_current_schema(monkeypatch, module)
        dept = DeptInfo.objects.create(name="独立导出部门", code="D900", rank=5)
        role = UserRole.objects.create(name="独立导出角色", code="R_STANDALONE")
        user = UserInfo.objects.create_user(username="standalone_user", password="Standalone@2026!")
        user.dept = dept
        user.nickname = "独立导出用户"
        user.save()
        user.roles.set([role])

        connection = module.connect(_test_dsn())
        try:
            exporter = module.Exporter(connection.cursor(), str(tmp_path), "", ["depts", "roles", "users"], print)
            exporter.run()
        finally:
            connection.close()

        user_row = next(row for row in exporter.data["users"] if row["username"] == "standalone_user")
        assert user_row["dept"] == "D900"
        # 多对多必须按自然键导出：解包错位（取实体名当表名）会让这里静默变空
        assert user_row["roles"] == ["R_STANDALONE"]
        role_row = next(row for row in exporter.data["roles"] if row["code"] == "R_STANDALONE")
        dept_row = next(row for row in exporter.data["depts"] if row["code"] == "D900")
        assert role_row["code"] == "R_STANDALONE"
        assert dept_row["name"] == "独立导出部门"

    def test_export_rejects_unknown_entity(self, tmp_path, capsys):
        module = _load_standalone()
        with pytest.raises(SystemExit):
            module.main(["--dsn", _test_dsn(), "--output", str(tmp_path), "--include", "nope"])


class TestScriptAndCommandConsistency:
    """独立脚本与 Django 命令的实体清单必须一致（两套实现的字段漂移守护）。"""

    def test_entity_field_lists_match(self):
        module = _load_standalone()
        assert set(module.TABLE_MAP) == set(lt.ENTITIES), "实体清单漂移"
        for name, spec in lt.ENTITIES.items():
            scripted = list(module.SCALAR_COLUMNS[name])
            command = [field for field in spec["fields"] if field not in ("password",) or name == "users"]
            for field in command:
                if field in ("signature",):
                    continue
                assert field in scripted or name in ("files",), f"{name}.{field} 未在独立脚本中搬运"

    def test_m2m_field_names_match(self):
        module = _load_standalone()
        scripted = {(item[0], item[1]) for item in module.M2M_MAP}
        expected = {(name, field) for name, spec in lt.ENTITIES.items() for field in spec.get("m2m", {})}
        missing = expected - scripted
        assert not missing, f"独立脚本缺少多对多映射：{sorted(missing)}"
