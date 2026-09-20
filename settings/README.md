# settings —— 「系统设置」业务 app

一句话：**这里是"设置什么"的业务实现；Django 工程配置在 `server/settings/` 包。**

- `models.py`：`Setting`（通用配置项存储：`name` / `value`（可加密）/ `category`），平台参数与个人配置的落库形态；
- `views/`：对外接口，按主题拆分——`basic`（基础设置）/ `security` / `email` / `sms` / `notify_im`（企业 IM）/ `ldap` / `block_ip`（IP 拦截）/ `settings`（个人配置）；
- `serializers/`、`migrations/`：跟随上述模型；
- 新增**业务可配置项**：在 `loadjson/systemconfig.json` 的默认值里登记（个人配置接口只更新既有键，禁止存储未知配置），
  运行时取值走 `common/core/config` 的 `SysConfig` / `UserConfig`。

同名易混：仓库顶层的 `server/settings/` 是 Django 工程配置包（`base.py` / `libs.py` …），
改数据库、缓存、CSP 等运行参数去那里；改"设置内容"来本目录。
