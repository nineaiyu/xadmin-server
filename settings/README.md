# settings —— 「系统设置」业务 app

一句话：**这里是"设置什么"的业务实现；Django 工程配置在 `server/settings/` 包。**

- `models.py`：`Setting`（通用配置项存储：`name` / `value`（可加密）/ `category`），平台参数与个人配置的落库形态；
- `views/`：对外接口，按主题拆分——`basic`（基础设置）/ `security` / `email` / `sms` / `notify_im`（企业 IM）/ `ldap` / `block_ip`（IP 拦截）/ `settings`（个人配置）；
- `serializers/`、`migrations/`：跟随上述模型；
- 新增**业务可配置项**：在 `loadjson/systemconfig.json` 的默认值里登记（个人配置接口只更新既有键，禁止存储未知配置），
  运行时取值走 `common/core/config` 的 `SysConfig` / `UserConfig`。

同名易混：仓库顶层的 `server/settings/` 是 Django 工程配置包（`base.py` / `libs.py` …），
改数据库、缓存、CSP 等运行参数去那里；改"设置内容"来本目录。

## 保存链路显式契约

19 个设置端点共用 `views/settings.py` 的 `BaseSettingViewSet.perform_update` 单一故障面，
与 `serializers/contract.py` 的 `SettingSaveContractMixin` 配对，行为如下：

1. **只存提交键**：仅 `request.data` 显式出现的键持久化——带 `default` 的可选字段
   未提交时不落库（PUT 同口径，设置页语义是「改了什么存什么」）；
2. **write_only 密文留空 = 不修改**：提交空值时回退已存值（`parse_serializer_data` 过滤）；
3. **响应载荷**：`set_response_data` 回写合并视图——未提交键取运行时当前值
   （`get_object` 快照）、变更键取新值；
4. **`serializer.change_fields`**：实际落库且值发生变化的键名列表；
5. **`post_save()` 钩子**：保存后联动失效 / 响应整形在此做——调用时 `change_fields`
   与 `response_data` 均已就绪，运行时 settings 由 pub/sub 订阅者异步热更。

二开提示：新序列化器请继承 `SettingSaveContractMixin`；旧私有名
`serializer._data` / `serializer._change_fields` 保留读写别名并告
DeprecationWarning，一个版本周期后移除。

## 运行时热更链路与删除闭环

- 保存：`post_save` 钩子发布 `(name, value)` 到 Redis pub/sub，各进程订阅者
  `setattr` 热更 django settings；
- **删除**：`post_delete` 钩子把该键恢复为静态配置默认值（`Setting.default_value`
  经 `common.injection.get_server_config` 读取，CONFIG 未知键回落 None）并广播，
  绕过 UI 的批量删除 / admin / 脚本删除同样闭环，不再出现「行已删、旧值残留」。
