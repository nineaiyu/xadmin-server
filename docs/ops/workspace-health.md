# 工作区跨仓一致性健康报告

一句话：把「同一份事实散落在多个仓库的副本是否仍然一致」收敛成一个可复跑、只读、
可上报的口子——`scripts/workspace_health.py`。

- **真跑口**：单仓 CI 里「缺兄弟仓」只能打印 `[skip]` 并放行（检出现实）；本脚本在
  **多仓同工作区**下真跑，缺仓一律记为 `missing`，不静默。
- **只读**：只读取被检查文件，绝不改写（写盘档位默认关闭，且需显式双开关）。
- **可上报**：`--format md --json <path>` 产出报告，配套 workflow 每周 upsert 固定标题 issue。

## 一、五个同源面 + 门禁聚合

| 面 | 真源 | 副本 / 对账 | 覆盖仓 |
|----|------|-------------|--------|
| `csp` | `server/settings/csp.py` 的 `_CSP_DIRECTIVES` | `xadmin-web/default.conf` 强制头、`xadmin-client/scripts/csp-page-server.mjs` 内嵌串 | server / web / client |
| `permissions` | `loadjson/menu.json` + `menumeta.json` | 前端 `scripts/check-menu-permissions.mjs` 退出码；menumeta 下发 i18n key ⊆ 前端 zh/en 语言包 | server / client |
| `contract` | `docs/schema/*.schema.json` | 客户端镜像 `contract/schema/`（按 normalize 口径忽略缩进/键序）+ `check-contract-sync.mjs` / `check-contract-usage.mjs` | server / client |
| `locale` | `xadmin-client/locales/zh-CN.yaml` ↔ `en.yaml` | 前端 `check-i18n-keys.mjs`；服务端 po 仅作 informational（语义不同，不判失败） | client / server |
| `versions` | `server/const.py` 的 `VERSION` | 客户端 `package.json`、`docs/guide/demo.md`、`installer/static.env`（`dev` 为开发豁免） | server / client / docs / installer |
| `gates` | — | 聚合四个仓库的**单仓门禁**（快档静态项；重档另见下文） | server / client / installer |

> 单仓门禁的「缺依赖即 `[skip]`」在本脚本里被翻译成状态而不是被吞掉：子脚本输出含
> `[skip]` → 未放行记 `missing`，已放行记 `degraded`；解析失败一律 `fail`（禁止
> `try/except` 转 skip）。

## 二、用法

```bash
# 工作区根默认为本仓上级目录（/path/to/xadmin）
python scripts/workspace_health.py

# 只跑版本矩阵与契约面；跳过门禁聚合
python scripts/workspace_health.py --only versions,contract

# 重档 + 允许写盘门禁；同时产出 JSON
python scripts/workspace_health.py --tier full --allow-write --format md --json report.json
```

| 选项 | 说明 |
|------|------|
| `--workspace-root` | 工作区根，默认 `REPO_ROOT.parent` |
| `--allow-missing <repo,...>` | 放行的缺失仓（默认放行 `xadmin-web`，它通常不在 git 检出内）；放行后降级 `degraded` 并在报告逐条列出 |
| `--only` / `--skip` | 按面筛选（`csp,permissions,contract,locale,versions,gates`） |
| `--tier fast\|full` | `fast` 只跑静态快门禁；`full` 追加重档 |
| `--allow-write` | 允许写盘门禁（仅 `--tier full` 生效） |
| `--json PATH` | 额外写出 JSON 报告 |
| `--format text\|md\|json` | 输出形态 |

**跨仓路径**用既定环境变量约定覆盖：`XADMIN_SERVER_DIR` / `XADMIN_CLIENT_DIR` /
`XADMIN_DOCS_DIR` / `XADMIN_INSTALLER_DIR` / `XADMIN_WEB_DIR`（缺省取工作区根下同名兄弟目录）。

### 退出码

| 码 | 含义 |
|----|------|
| `0` | 全绿（`degraded` 不阻断） |
| `1` | 检出真实漂移 / 违例（含矩阵空格、面级异常归一） |
| `2` | 有 `missing` 且未放行（含未被任何选中面引用的缺仓） |

### 档位与写盘

- `fast`（默认）：四仓快门禁——服务端 `scripts/check_*.py` 静态项、客户端
  `scripts/check-*.mjs` 秒级项、installer `bash -n` shell 语法；均为只读。
- `full`：追加重档（服务端 `mypy` / `pytest`、客户端 `typecheck` / `vitest` /
  `build` / E2E）。`writes_disk=True` 的门禁（构建、E2E）**只有** `--tier full
  --allow-write` 才执行，否则记 `degraded` 并说明原因。

## 三、报告与防漏检

运行前打印「面 × 仓」矩阵骨架，跑完逐格填状态：

- 状态五值：`pass` / `fail` / `missing` / `degraded` / `n/a`（面在该仓无适用项）。
- **适用格为空 → 判 `fail`**（矩阵空格 = 校验漏洞，不允许「检了但没结论」）。
- 面执行期抛异常 → 归一为 `fail`（不是跳过），保证不因一个面的 bug 而假绿。
- 报告自检行：生成时间 / 检出仓 / 缺失仓 / 放行缺失 / 面状态 / 建议动作。

## 四、与单仓守护的关系

| 场景 | 单仓 CI（`check_*` / 守护测试） | 本脚本（多仓工作区 / 周报） |
|------|-------------------------------|------------------------------|
| 缺兄弟仓 | `[skip]` 放行（单仓检出的必然） | `missing`（退出码 2），除非显式放行 |
| 同源漂移 | 有守护测试的片（如 CSP 三处）拦截 | 全五面无差别拦截 |
| 运行时机 | 每次 PR | 每周定时 + 手动，只报告不阻断 |

一句话：**单仓守护拦「改一处」，本脚本拦「多仓副本互相漂移」**，两者互补而非替代。

## 五、CI 行为

`.github/workflows/workspace-health.yml`：每周一（UTC 01:00）与手动触发，把四仓检出到
同一工作区后跑 `--tier fast`，用 `github-script` 以固定标题
「工作区跨仓一致性健康报告（周报）」upsert 一个 issue（更新正文 + 追加评论），
并把报告写入 job summary。**该 job 只报告不阻断**：健康漂移不使 CI 变红，由 issue 跟踪。

## 六、首跑记录（工作区实测）

`--tier fast` 全绿：6 个面 36 条检查，`pass 35 / fail 0 / missing 0 / degraded 1`
（`degraded` 为 fast 档主动跳过 6 个 full 门禁的说明项）。要点：

- **契约 schema 字节数不同 ≠ 漂移**：`search-columns` / `search-fields` / `ws-frame`
  等镜像与服务端源文件大小存在差异，但按 `check-contract-sync.mjs` 的 normalize 口径
  （忽略缩进与键序）逐份比对**语义完全一致**，属格式差异，非真漂移。
- `versions`：服务端 `VERSION` 与客户端 / 文档站一致；installer 的 `dev` 命中开发豁免。
- `locale`：前端 zh/en 词条 key 集合完全对称；服务端 po 仅记录规模。

> 复跑：`python scripts/workspace_health.py --workspace-root <工作区根> --tier fast`。
> 单测见 `tests/unit/scripts/test_workspace_health.py`（用 tmp_path 造迷你仓树，覆盖
> 解析函数、缺仓降级语义、矩阵空格与面级异常收敛，不依赖真实五仓）。
