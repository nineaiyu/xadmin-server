# 贡献指南（CONTRIBUTING）

感谢参与 xadmin-server 开发。请先阅读[文档中心](docs/README.md)与[架构总览](docs/architecture/overview.md)，再开始编码。

## 1. 分支模型

| 分支 | 用途 | 保护 |
|------|------|------|
| `main` | 稳定发布分支，tag `v*` 触发发布流水线 | 禁止直推，经 dev 验证后合并 |
| `dev` | 集成分支，CI 门禁挂载于此 | PR 合入；push 自动触发 test/lint |
| `feat/*` `fix/*` `docs/*` | 功能/修复/文档开发分支 | 从 dev 切出，合回 dev |

升级、大规模重构等高风险变更（如 Django 大版本，见 ADR-004）必须在独立分支 + 全量门禁通过后合入。

## 2. 提交信息规范

遵循 Conventional Commits（commitlint 校验，header ≤108 字符）：

```
<type>(<scope>): <subject>
```

- 允许的 type：`feat` `fix` `perf` `style` `docs` `test` `refactor` `build` `ci` `chore` `revert` `wip` `workflow` `types` `release`；
- scope 用模块名（如 `system`、`common`、`modelset`、`notifications`）；
- 关联半年规划任务的，在 subject 或 body 中带上任务号（如 `T2.1`）或债务号（`TD-xx`）。

示例：

```
feat(system): 用户列表接口支持 with_meta=1 内联元数据（T3.2）
fix(common): 操作日志中间件动词方法无兜底导致 500（TD-24）
```

## 3. 开发环境

```shell
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp config_example.yml config.yml    # 本地开发建议 DB_ENGINE: sqlite3
python manage.py migrate && python utils/init_data.py
python manage.py start all
```

详见 [docs/ops/deployment.md](docs/ops/deployment.md)。

## 4. 提交前门禁（本地自查，CI 同款）

| 门禁 | 命令 | 说明 |
|------|------|------|
| 代码风格 | `ruff check .` | lint.yml 强制 |
| 测试 + 覆盖率 | `pytest -n auto --cov --cov-fail-under=75` | 覆盖率门禁 75%（T4.1） |
| 跨 app 引用 | `python scripts/check_cross_app_imports.py` | 业务层必须走 `<app>.services` 契约层 |
| 契约测试 | `pytest tests/unit/common/test_metadata_schema.py` | 改动元数据接口时必跑，schema 同步更新 [docs/schema/](docs/schema/) |

约束清单：

- 新增错误码必须先登记 [docs/exception-handling.md](docs/exception-handling.md)；未预期异常返回通用文案，详情只进日志；
- 改动权限/缓存相关代码，先读 [docs/architecture/permission.md](docs/architecture/permission.md) 与 [docs/architecture/cache.md](docs/architecture/cache.md)（绕过 ORM 的信号失效红线）；
- 涉及模型字段变更，评估索引必要性并对照 [docs/architecture/indexes.md](docs/architecture/indexes.md) 评审记录；
- 生产安全相关配置（密钥、密码、认证）一律配置化，禁止硬编码默认值（见 [docs/security-review.md](docs/security-review.md)）。

## 5. ADR 流程

影响架构方向的决策（协议选型、依赖大版本、组件去留、安全基线变更）需要 ADR：

1. 复制现有格式（见 [docs/adr/](docs/adr/)）新建 `ADR-00N-<主题>.md`，编号顺延；
2. 内容包含：背景 → 备选方案 → 决策与理由 → 实施项 → 后果；
3. 在 [docs/README.md](docs/README.md) 的 ADR 索引表登记；
4. 重大技术选型建议先提 Issue 评审再落 ADR。

## 6. PR 流程

1. 从 `dev` 切出分支，提交前跑齐 §4 门禁；
2. 使用 PR 模板（`.github/PULL_REQUEST_TEMPLATE.md`）填写变更说明与自查项；
3. 至少一项 CI 门禁通过后请求合并；破坏性变更需在标题标注 `!` 或 body 说明迁移方案；
4. 合并方式默认 squash；关联 Issue 请在 body 写 `Closes #xx`。

## 7. 安全问题

请勿通过公开 Issue/PR 报告安全漏洞。安全修复参考 [docs/security-review.md](docs/security-review.md) 的基线与档案。
