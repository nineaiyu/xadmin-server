# -*- coding: utf-8 -*-
"""server/conf.py：Config 取值优先级与类型转换、ConfigManager 多来源加载。"""
import pytest

from server.conf import Config, ConfigManager, DoesNotExist, import_string

# 说明：load_user_config 被 tests/settings_test.py 全局替换为测试配置，
# 不直接对入口函数断言，回退链以组件方法重建验证（见 TestConfigManager 末尾）。


class TestImportString:
    def test_valid_path(self):
        from server.conf import Config as Target
        assert import_string("server.conf.Config") is Target

    def test_no_dot_raises_import_error(self):
        with pytest.raises(ImportError):
            import_string(" nodot ")

    def test_missing_attribute_raises_import_error(self):
        with pytest.raises(ImportError):
            import_string("server.conf.NotExists")


class TestConfigGet:
    def test_default_value_used(self, monkeypatch):
        monkeypatch.delenv("DEBUG", raising=False)
        config = Config()
        assert config.get("DEBUG") is False

    def test_config_file_overrides_default(self, monkeypatch):
        monkeypatch.delenv("DEBUG", raising=False)
        config = Config({"DEBUG": True})
        assert config.get("DEBUG") is True
        assert config["DEBUG"] is True
        assert config.DEBUG is True

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("DEBUG", "true")
        config = Config()
        assert config.get("DEBUG") is True

    def test_config_file_overrides_env(self, monkeypatch):
        monkeypatch.setenv("HTTP_LISTEN_PORT", "9999")
        config = Config({"HTTP_LISTEN_PORT": 1234})
        assert config.get("HTTP_LISTEN_PORT") == 1234

    def test_get_with_explicit_default(self, monkeypatch):
        monkeypatch.delenv("NOT_EXIST_KEY", raising=False)
        config = Config()
        assert config.get("NOT_EXIST_KEY", "fallback") == "fallback"

    def test_getitem_missing_key_returns_none(self):
        # dict 语义变更：未配置键返回 None 而非 KeyError
        assert Config()["NOT_EXIST_KEY"] is None

    def test_old_config_map_fallback(self, monkeypatch):
        monkeypatch.delenv("OLD_KEY", raising=False)
        config = Config()
        config.old_config_map = {"NEW_KEY": "OLD_KEY"}
        config["OLD_KEY"] = "legacy"
        assert config.get("NEW_KEY") == "legacy"


class TestConvertType:
    @pytest.fixture
    def config(self):
        return Config()

    def test_bool_true_variants(self, config):
        assert config.convert_type("DEBUG", "true") is True
        assert config.convert_type("DEBUG", "1") is True
        assert config.convert_type("DEBUG", "True") is True

    def test_bool_false_variants(self, config):
        assert config.convert_type("DEBUG", "false") is False
        assert config.convert_type("DEBUG", "0") is False
        assert config.convert_type("DEBUG", "anything-else") is False

    def test_bool_native_value_passthrough(self, config):
        assert config.convert_type("DEBUG", True) is True

    def test_int_conversion(self, config):
        assert config.convert_type("HTTP_LISTEN_PORT", "8897") == 8897

    def test_list_json_string(self, config):
        assert config.convert_type("XADMIN_APPS", '["a", "b"]') == ["a", "b"]

    def test_list_invalid_json_kept_as_string(self, config):
        assert config.convert_type("XADMIN_APPS", "not-json") == "not-json"

    def test_dict_json_string(self, config):
        assert config.convert_type("API_LOG_IGNORE", '{"k": ["GET"]}') == {"k": ["GET"]}

    def test_unknown_key_passthrough(self, config):
        assert config.convert_type("UNKNOWN_KEY", "raw") == "raw"


class TestConfigManager:
    @pytest.fixture
    def manager(self, tmp_path):
        return ConfigManager(root_path=str(tmp_path))

    def test_from_mapping(self, manager):
        manager.from_mapping(DEBUG=True, HTTP_LISTEN_PORT=1234, ignore_lower="skip")
        assert manager.config["DEBUG"] is True
        assert manager.config["HTTP_LISTEN_PORT"] == 1234
        assert "ignore_lower" not in manager.config

    def test_from_object_skips_lowercase(self, manager):
        class Obj:
            FOO = "bar"
            hidden = "no"

        manager.from_object(Obj())
        assert manager.config["FOO"] == "bar"
        assert "hidden" not in manager.config

    def test_from_object_by_dotted_string(self, manager):
        manager.from_object("server.conf.Config")
        assert manager.config["DEBUG"] is False

    def test_from_pyfile(self, manager, tmp_path):
        py = tmp_path / "config.py"
        py.write_text("DEBUG = True\nPORT = 1234\n")
        assert manager.from_pyfile("config.py") is True
        assert manager.config["DEBUG"] is True
        assert manager.config["PORT"] == 1234

    def test_from_pyfile_missing_silent(self, manager):
        assert manager.from_pyfile("missing.py", silent=True) is False

    def test_from_yaml(self, manager, tmp_path):
        yml = tmp_path / "config.yml"
        yml.write_text("DEBUG: true\nXADMIN_APPS:\n  - system\n")
        assert manager.from_yaml("config.yml") is True
        assert manager.config["DEBUG"] is True
        assert manager.config["XADMIN_APPS"] == ["system"]

    def test_from_yaml_empty_file(self, manager, tmp_path):
        yml = tmp_path / "config.yml"
        yml.write_text("")
        assert manager.from_yaml("config.yml") is True

    def test_from_yaml_missing_raises(self, manager):
        with pytest.raises(IOError):
            manager.from_yaml("missing.yml")

    def test_from_yaml_missing_silent(self, manager):
        assert manager.from_yaml("missing.yml", silent=True) is False

    def test_from_json(self, manager, tmp_path):
        js = tmp_path / "config.json"
        js.write_text('{"DEBUG": true}')
        assert manager.from_json("config.json") is True
        assert manager.config["DEBUG"] is True

    def test_from_json_missing_silent(self, manager):
        assert manager.from_json("missing.json", silent=True) is False

    def test_load_from_yml_prefers_config_yml(self, manager, tmp_path):
        (tmp_path / "config.yml").write_text("HTTP_LISTEN_PORT: 8801\n")
        (tmp_path / "config.yaml").write_text("HTTP_LISTEN_PORT: 8802\n")
        assert manager.load_from_yml() is True
        assert manager.config["HTTP_LISTEN_PORT"] == 8801

    def test_load_from_yml_fallback_yaml(self, manager, tmp_path):
        (tmp_path / "config.yaml").write_text("HTTP_LISTEN_PORT: 8802\n")
        assert manager.load_from_yml() is True
        assert manager.config["HTTP_LISTEN_PORT"] == 8802

    def test_load_from_yml_none_found(self, manager):
        assert manager.load_from_yml() is False

    def test_load_user_config_fallback_chain(self, tmp_path, monkeypatch):
        """回退链：config.py → config 模块 → config.yml/yaml → 报 ImportError。

        tests/settings_test.py 已全局把 load_user_config 替换为返回测试配置，
        这里按 conf.py 原始语义用组件方法显式重建回退链验证。
        """
        from server.conf import PROJECT_DIR

        # 空目录：三个来源全部落空 → 等效于 load_user_config 抛 ImportError
        manager = ConfigManager(root_path=str(tmp_path / "nope"))
        assert manager.from_pyfile() is False
        monkeypatch.setattr("server.conf.PROJECT_DIR", str(tmp_path))
        assert manager.load_from_object() is False
        monkeypatch.undo()
        assert manager.load_from_yml() is False

    def test_load_user_config_from_yaml(self, tmp_path):
        manager = ConfigManager(root_path=str(tmp_path))
        (tmp_path / "config.yml").write_text("HTTP_LISTEN_PORT: 8803\n")
        # load_user_config 的第三回退分支：从 config.yml 加载
        assert manager.load_from_yml() is True
        assert manager.config["HTTP_LISTEN_PORT"] == 8803


class TestMisc:
    def test_does_not_exist_is_exception(self):
        assert issubclass(DoesNotExist, Exception)

    def test_repr(self):
        assert "Config" in repr(Config())
