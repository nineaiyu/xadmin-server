# -*- coding: utf-8 -*-
"""modelset 权限钩子宿主覆写对账守护。

``ChangeRolePermissionAction.assert_can_change_permissions`` 默认放行，宿主
ViewSet 必须按对象级策略覆写；本测试静态对账「所有混入该 mixin 的具体
ViewSet 都覆写了钩子」，防止未来新宿主静默漏覆写（漏写 = 对象级授权缺位）。
"""

from system.services.modelset import ChangeRolePermissionAction


def _all_subclasses(base: type) -> list[type]:
    found: list[type] = []
    stack = list(base.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        found.append(cls)
    return found


class TestPermissionHookOverride:
    def test_all_hosts_override_permission_hook(self):
        # 显式导入已知宿主模块（URLconf 加载外的兜底；新宿主经路由加载自动纳入）
        import identity.views.admin.user  # noqa: F401

        hosts = [cls for cls in _all_subclasses(ChangeRolePermissionAction)]
        assert hosts, "应至少发现一个宿主 ViewSet"
        missing = [cls.__qualname__ for cls in hosts if "assert_can_change_permissions" not in cls.__dict__]
        assert not missing, f"混入 ChangeRolePermissionAction 但未覆写授权目标校验钩子: {missing}"

    def test_default_hook_is_passthrough_contract(self):
        """默认放行是 mixin 契约（宿主按对象级策略覆写），docstring 必须声明。"""
        import inspect

        doc = inspect.getdoc(ChangeRolePermissionAction.assert_can_change_permissions) or ""
        assert "覆写" in doc or "override" in doc.lower()
