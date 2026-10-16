# xadmin 5.0.1 发布清单（发版台账）

> 状态行：2026-10-16 代码改动已推送（server/dev、client/dev、installer/main、web/main、docs/main），
> 待按序打 tag；「四、发布后验证」的证据在 tag 完成后回填。
> 上游背景：5.0.0 的发布链路断在「tag 推送顺序」与「镜像同步」，另有 5 处安装侧缺陷——本次全部修复。

## 一、发布结论

**可以发布**。这是修复版：**无表结构变更、无接口变更**，前后端版本号同步升至 `5.0.1`。
发布内容全部来自 5.0.0 上线实测（一台干净 CentOS 7 机器上执行一键安装）暴露的问题。

## 二、改动清单

| 仓库 | 分支 | 内容 |
|------|------|------|
| `xadmin-server` | dev | `upgrade_check` 空库判定修复（+4 组用例）；镜像构建关闭构建溯源附件；版本号与文档事实同步（const.py / README / CHANGELOG / deployment.md） |
| `xadmin-client` | dev | 镜像构建关闭构建溯源附件；版本号同步（package.json / public/platform-config.json / README） |
| `xadmin-installer` | main | 安装器 5 处缺陷修复 + `set -u` 运行期守护用例；镜像同步改造（crane 按平台 manifest 重建 index + 等待源镜像 + 按版本补跑入口）；AGENTS 补发布链路口径 |
| `xadmin-web` | main | 镜像构建前等待 client 镜像（消除同批发版顺序敏感）；镜像构建关闭构建溯源附件 |
| `xadmin-docs` | main | 一键安装页版本号同步；镜像构建关闭构建溯源附件 |

修复要点（对使用者可见）：

1. **首次安装可用**：5.0.0 安装器在干净机器上必然中断（`ALLOWED_HOSTS` 缺失、数据目录属主、
   空库体检判定、`$1` 未绑定、celery 健康检查路径），本次逐项修复。
2. **镜像可获取**：阿里云镜像仓此前没有 5.0.0；同步链路改造后 5.0.0 已补齐，5.0.1 起不再复发。
3. **升级前体检**：全新空库不再被判 `schema-drift`（改为 `needs-migrate`）。

## 三、验证状态（发布前）

| 面 | 命令 / 方式 | 结果 |
|----|-------------|------|
| 后端单元全量 | `uv run pytest tests/unit -n auto` | ✅ 首跑 exit=0（一轮出现 `test_reentrant_lock.py` 顺序性失败，隔离复跑失败用例逐次漂移 → 既有隔离问题，见「六、遗留」） |
| 后端静态门禁 | `ruff format --check` / `ruff check` / `mypy`（改动文件） | ✅ 全绿 |
| 后端脚本门禁 | 跨 app 导入 / 文件行数 / 函数长度 / 缓存键 `--strict` / 文档事实 / 文档索引 / 文档路径 / ADR 状态 / 文档体积 | ✅ 9/9 通过（文档事实按 `const:VERSION` 同步 2 处） |
| 迁移漂移 | `manage.py makemigrations --check --dry-run` | ✅ `No changes detected` |
| 安装器语法与运行期守护 | 全量 `bash -n` + `.github/scripts/set-u-guard.sh`（9 项） | ✅ 全绿；守护已「先红后绿」验证（回退任一修复即变红） |
| 安装器端到端 | 干净 CentOS 7（bash 4.2）执行 `./xadmin.sh install` + `start` | ✅ 安装完成、5 容器 healthy、`/api/common/api/health` 四项 `true`、页面 200、86 迁移已应用 / 0 待应用、菜单 714 |
| 镜像同步 | `gh workflow run sync-image-mirror.yml -f version=5.0.0` | ✅ 同步成功；阿里云仓 server/web/docs 5.0.0 各 2 平台（amd64/arm64）、无溯源附件 |
| CI（推送后） | server `Unit Tests` / `Lint Code`；client `Lint Code` / `E2E Tests`；installer `scripts-check` / `sync-image-mirror` | ✅ 全绿 |

## 四、发布步骤（tag 顺序）

> 只有用户明确指示时才打 tag。**顺序即正确性**：web 依赖 client 镜像，installer 的同步依赖
> server/web/docs 镜像；虽然 web 与 installer 的工作流已内置等待，tag 仍按依赖顺序推送更稳。

1. `v5.0.1`（`xadmin-server`，main）：触发版本门禁（tag ↔ `const.py` ↔ client `package.json`）→
   依赖审计 + 镜像扫描 → 多架构构建与推送 → 上传 SBOM / OpenAPI 到 Release。
2. `v5.0.1`（`xadmin-client`，main）：构建并推送 `nineaiyu/xadmin-client:5.0.1`。
3. `v5.0.1`（`xadmin-web`，main）：构建前等待 client 镜像，产出 `nineaiyu/xadmin-web:5.0.1`。
4. `v5.0.1`（`xadmin-docs`，main）：产出 `nineaiyu/xadmin-docs:5.0.1`。
5. `v5.0.1`（`xadmin-installer`，main）：生成安装包资产（`static.env` 的 `VERSION` 按 tag 写入）
   → 触发镜像同步（等待 server/web/docs 镜像产出后搬运）。
   - 补救：`gh workflow run sync-image-mirror.yml -R nineaiyu/xadmin-installer -f version=5.0.1`
     （tag 触发的 run 不能用 rerun 修复——rerun 走的是 tag 上的旧工作流）。

## 五、发布后验证

1. Docker Hub：`nineaiyu/{xadmin-server,xadmin-client,xadmin-web,xadmin-docs}:5.0.1` 可拉取；
2. 阿里云仓：`registry.cn-beijing.aliyuncs.com/nineaiyu/{xadmin-server,xadmin-web,xadmin-docs}:5.0.1`
   各含 `linux/amd64` 与 `linux/arm64`（且不含构建溯源附件）；
3. 安装包资产：下载 `xadmin-installer-v5.0.1.tar.gz`，确认 `scripts/utils.sh` 含
   `ensure_allowed_hosts` / `ensure_data_dir_owner` / `is_fresh_database`，`scripts/utils.sh` 的
   `get_docker_compose_cmd_line` 用 `${1-}`；
4. 干净机器一键安装复跑：`VERSION=v5.0.1 curl … quick_start.sh | bash` → 5 容器 healthy；
5. 存量 5.0.0 环境升级：`./xadmin.sh upgrade v5.0.1` → `upgrade_check` 为 `up-to-date`。

## 六、遗留

- `tests/unit/common/test_reentrant_lock.py` 为**既有**顺序性失败（与本次改动无关：含本次改动的
  首跑为 exit=0，隔离复跑失败用例逐次漂移）；待单独排查测试隔离/事务边界。
- 发布说明（GH Release）由 release-drafter 按标签生成；五仓模板已含「⚠️ 升级注意」分组。
