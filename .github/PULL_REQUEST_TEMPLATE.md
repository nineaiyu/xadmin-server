<!-- 标题格式：<type>(<scope>): <subject>，与 commitlint 一致 -->

## 变更说明

<!-- 做了什么、为什么；关联任务号（T*.x / TD-xx）或 Issue（Closes #xx） -->

-

## 变更类型

- [ ] feat 新功能
- [ ] fix 缺陷修复
- [ ] refactor 重构（无行为变更）
- [ ] perf 性能优化
- [ ] docs 文档
- [ ] test 测试补齐
- [ ] ci/build 工程化
- [ ] ⚠️ 破坏性变更（在下方说明迁移方案）

## 自查清单

- [ ] `ruff check .` 通过
- [ ] `pytest -n auto --cov --cov-fail-under=82` 全绿
- [ ] `python scripts/check_file_length.py` 通过（新增 >500 行文件即失败，存量基线只减不增）
- [ ] `python scripts/check_cross_app_imports.py` 通过（业务层走 `<app>.services`）
- [ ] 改动元数据接口时：`docs/schema/` 已同步 + 契约测试通过
- [ ] 新增错误码已登记 `docs/exception-handling.md`
- [ ] 涉及权限/缓存改动：已读 architecture/permission.md 与 cache.md 红线
- [ ] 涉及生产配置：无硬编码密钥/密码/认证默认值
- [ ] 断言与环境无关（locale / 时区 / 排序等；CI 无 `.mo` 环境必须同样通过）

## 验证方式

<!-- 本地如何验证（命令/操作路径/截图） -->
