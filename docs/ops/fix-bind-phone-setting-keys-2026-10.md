# 安全设置「绑定手机」配置键错写修复与数据清理（2026-10）

> 2026-10 代码评审的唯一 P0 项。
> 缺陷：`SecurityBindPhoneAuthSerializer` 序列化器字段误用 `SECURITY_BIND_EMAIL_*` 前缀，
> 「绑定手机」页签实际读写邮箱配置——关手机绑定会同步关掉邮箱绑定；
> 运行时真正消费的 `SECURITY_BIND_PHONE_*`（`identity/views/auth/verify_code.py`）在 UI 上不可配。

## 1. 缺陷机理

- 设置项按 `Setting.name` 唯一 upsert（`settings/models.py` `update_or_create`），
  序列化器字段名即 Setting 行名与运行时 `settings` 键；
- 修复前「绑定手机」页签（`/api/settings/bind/phone`）读写的 4 个键全部是
  `SECURITY_BIND_EMAIL_ACCESS/CAPTCHA/TEMP_TOKEN/ENCRYPTED_ENABLED`，
  与「绑定邮箱」页签完全同键；
- 因此通过「绑定手机」页签做的任何修改，实际改的都是邮箱绑定配置。

## 2. 影响评估

| 场景 | 影响 |
|---|---|
| 只用过「绑定邮箱」页签 | 无影响（键名正确） |
| 用过「绑定手机」页签改配置 | 实际改了邮箱绑定配置；且手机绑定的预期值从未落库（运行时回落默认值 True 或 config.yml 显式值） |
| 未改过安全设置 | 无影响 |

无孤立的错误键名数据行：错写产生的行名就是邮箱键名本身（同名覆盖），
唯一的残留是这些行的 `category` 列可能被标成 `security_bind_phone_auth`。

## 3. 数据核查 SQL

```sql
-- 3.1 查被「绑定手机」页签最后写入的邮箱配置行（category 残留）
SELECT name, value, category, updated_time
FROM settings_setting
WHERE name LIKE 'SECURITY_BIND_EMAIL_%'
  AND category = 'security_bind_phone_auth';

-- 3.2 查当前邮箱 / 手机绑定配置实际生效值（结合运行时默认值判断是否被误改）
SELECT name, value, category, updated_time
FROM settings_setting
WHERE name LIKE 'SECURITY_BIND_EMAIL_%' OR name LIKE 'SECURITY_BIND_PHONE_%'
ORDER BY name;
```

误改判定：对比 `updated_time` 与管理操作日志（操作日志模块可按
`SECURITY_BIND_EMAIL_` 关键字检索），确认是否有人在「绑定手机」页签保存过配置。

## 4. 清理 / 迁移步骤

1. **修正 category 残留**（仅分类元数据，值不受影响，可随时执行）：

   ```sql
   UPDATE settings_setting
   SET category = 'security_bind_email_auth'
   WHERE name LIKE 'SECURITY_BIND_EMAIL_%'
     AND category = 'security_bind_phone_auth';
   ```

2. **补齐手机绑定配置**：升级部署后，由管理员进入
   系统设置 → 安全设置 →「绑定手机」页签，按预期值重新保存一次
   （修复后会正常写入 `SECURITY_BIND_PHONE_*` 行）。
   未保存的键继续走默认值（全部为开启，见 `server/conf/settings_defaults.py`）。

3. **回访业务方**：如 3.1 查到误改痕迹，确认邮箱绑定开关的当前值是否符合预期，
   不符合则在「绑定邮箱」页签改回。

## 5. 回归保障

- `tests/integration/test_settings_bind_auth.py`：
  序列化器字段面前缀校验、双页签读写互不干扰、运行时消费键全部可配；
- 部署后手工验证：分别在「绑定手机」「绑定邮箱」页签切换开关，
  确认另一页签的值不随之变化。
