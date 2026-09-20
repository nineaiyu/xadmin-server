# server/settings —— Django 工程配置包（不是业务 app）

一句话：**这里是"怎么跑"的配置；业务意义上的"系统设置"在仓库顶层的 `settings/` app。**

| 这里是 | 不是 |
|--------|------|
| Django 工程配置包：`base.py`（核心）/ `custom.py`（部署覆盖）/ `libs.py`（第三方库）/ `logging.py` / `setting.py` 由 `__init__.py` 拼装 | 不是 Django app，没有 models / migrations / views |
| 数据库、缓存、CSP、DRF、日志、中间件等**运行参数** | 系统配置项（`SystemConfig` 语义）、邮件/短信/IM/LDAP/安全设置**业务接口** |

- 同名易混：仓库顶层的 `settings/` 是「系统设置」业务 app（含 `models` / `views` / `serializers` / `migrations`），
  见 `settings/README.md`；
- 二开修改配置的常规路径：优先改 `config.yml`（见 `config_example.yml` 取值链说明），
  本包只承接"必须写死在工程侧"的装配；
- 目录定位说明同见 `docs/architecture/overview.md` §二。
