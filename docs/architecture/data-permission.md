## 数据权限控制

原理： 数据权限是通过 ```queryset.filter``` 来实现。

在 ```settings.py``` 定义了一个全局的```DEFAULT_FILTER_BACKENDS```,
具体方法```common.core.filter.BaseDataPermissionFilter```

具体的实现方式参考```common.core.filter.get_filter_queryset```

如果自定义方法使用全局的filter,可以通过下面获取queryset对象

```python
filter_queryset = self.filter_queryset(self.get_queryset())
```

1. 权限模式, 且模式表示数据需要同时满足规则列表中的每条规则，或模式即满足任意一条规则即可
2. 若存在菜单权限，则该权限仅针对所选择的菜单权限生效

## 如何使用？本次使用是查询指定条件用户的数据权限

### 1. 在前端页面菜单中，添加数据权限，然后选择菜单为查询用户，规则选择

![add-data-permission.png](../imgs/data-permission/add-data-permission.png)

![add-data-permission-rules.png](../imgs/data-permission/add-data-permission-rules.png)

将该数据权限分配给用户即可

## 排障：用户看到空集 / 规则不生效

数据权限是 fail-closed 口径（无适用授权即空集），排查分三步：

1. **规则存储体检**——与写入侧 `validate_rules` 同源校验存量规则，列出非法授权及原因
   （报错带规则序号与字段/匹配符上下文），并给出「不生效」提示（`[WARN]`，不影响退出码）：

   ```bash
   python manage.py audit_data_permission_rules               # 只列出，不改库
   python manage.py audit_data_permission_rules --strict      # CI 门禁：发现非法即非零退出
   python manage.py audit_data_permission_rules --deactivate  # 非法授权整体停用（is_active=False）
   ```

   `[WARN]` 覆盖三类「规则合法但对绑定对象恒为空集」的配置：未绑定任何用户/部门；
   「主管部门」类规则绑定对象中没有任何部门主管；规则引用的用户/部门/角色/菜单已被删除。
   提示只列不改（部分可能是有意配置），修正后重跑确认。

2. **用户视角试算**——数据权限页的「试算」面板按目标用户实跑 `get_filter_queryset`，
   展示实际生效的授权、规则可读文案与命中结果，回答「他为什么能/不能看到这条数据」。

3. **运行时日志**——读侧对坏规则 fail-closed 时会打 `data scope rule ... fail-closed` 告警
   （字段不存在 / 无法编译），绑定用户看到空集时可从日志定位到具体模型与字段。