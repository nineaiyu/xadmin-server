# 内核分发包（xadmin-common）发布渠道与版本策略

> **定位**：回答「内核包怎么发、发到哪、宿主怎么拿到、升级与回滚怎么做」。
> 发行形态、宿主对接面与 settings 契约见 [框架内核独立分发包](../architecture/kernel-package.md)；
> 逐版本变更见包内 `packages/xadmin-common/CHANGELOG.md`。

## 一、发布渠道

内核包是**标准 Python 分发包**（wheel + sdist，分发名 `xadmin-common`，导入名 `common`），
渠道形态按团队基础设施三选一——发布脚本与宿主安装命令只差「索引地址」一处：

| 形态 | 适用场景 | 宿主安装 | 说明 |
|---|---|---|---|
| 私有 PyPI（devpi / Artifactory / Nexus / 云厂商制品库） | 团队已有制品库（推荐） | `uv add --index <name> xadmin-common` | 需具备 simple 索引 + legacy 上传端点；凭据走 uv 环境变量 |
| 简单静态索引（pypi-simple 等 + 反向代理） | 只有文件服务器 / 静态站点 | `pip install --extra-index-url <url> xadmin-common` | 上传端点需自行暴露；适合轻量自建 |
| 离线 wheel 分发 | 完全隔离的内网环境 | `pip install ./xadmin_common-X.Y.Z-py3-none-any.whl` | 无索引；升级 = 拷贝新 wheel + 重装，构建机与目标机需同 Python 大版本 |

**地址与凭据**（都不进仓库）：

| 变量 | 用途 |
|---|---|
| `XADMIN_COMMON_PUBLISH_URL`（或 `publish --url`） | 上传端点（legacy API，如 `https://pypi.example.com/legacy/`） |
| `XADMIN_COMMON_INDEX`（或 `publish --index`） | uv 配置里登记的 index 名（`[[tool.uv.index]]` → `publish-url`） |
| `UV_PUBLISH_TOKEN` | 上传凭据（Basic Auth 形态用 `UV_PUBLISH_USERNAME` / `UV_PUBLISH_PASSWORD`） |

**宿主（外部 / 二开项目）接入**：在宿主 `pyproject.toml` 登记索引来源（下例；pip 侧等价写法
是 `--extra-index-url`）：

```toml
[[tool.uv.index]]
name = "corp"
url = "https://pypi.example.com/simple"

[tool.uv.sources]
xadmin-common = { index = "corp" }
```

> 本仓（xadmin-server）自身**不经过私有源**：`xadmin-common` 是 `[tool.uv.workspace]` 成员，
> 以 editable 路径依赖消费（改内核源码即时生效，见 [kernel-package.md](../architecture/kernel-package.md) §一）。
> 私有源服务的是工作区之外、需要独立升级内核版本的宿主。

## 二、版本策略

- **单一事实源**：`packages/xadmin-common/common/__init__.py` 的 `__version__`；成员
  `pyproject.toml` 只声明 `dynamic = ["version"]`（`[tool.hatch.version]` 指向该文件），
  wheel / sdist 元数据由构建后端取值——**任何地方都不重复写版本号**。
  动态版本的连带效果：`uv.lock` **不记录**工作区成员的版本行，改版本号**无需**重锁
  （若 `uv lock --check` 报需更新，说明 pyproject 又写回了 `version`，属配置回退）；
  守护测试 `tests/unit/test_dependency_manifest.py` 双向核对这一形态；
- **与平台版本解耦**：内核版本（packaging 版本，如 `0.2.0`）独立于服务端整包版本
  （`server/const.py` 的 `VERSION`）；内核可以在平台版本不变的情况下单独发版；
- **语义化版本**（0.x 阶段）：`minor` = 行为 / 契约面变更（含破坏性变更，0.x 允许），
  `patch` = 兼容性修复，`major`（1.0 起）= 破坏性变更需升主版本；
- **什么算破坏性变更**（发版时在 changelog 显式标注）：settings 契约面的键增删 /
  缺省语义变更（含「必给 ↔ 有缺省」互换）、`kernel_setting` 等公开 API 的签名与语义、
  内核模型迁移、契约面（`common/contracts.py` 白名单）的增删；
- **tag 命名**：`common-vX.Y.Z`（与平台 tag 区分）；changelog 首条必须等于当前版本
  （`scripts/release_kernel.py check` 会拦）；
- **changelog 纪律**：形态遵循 Keep a Changelog（`变更 / 新增 / 修复` 三类），未发布改动
  归入 `## [Unreleased]`；发布时把条目移出并补版本号与日期。

## 三、发版流程

```shell
# 0. 变更已入库：内核代码 + 守护测试 + 全量 pytest（内核影响全部宿主，勿只跑改动面）
# 1. 登记变更：packages/xadmin-common/CHANGELOG.md 顶部条目（版本 / 日期 / 内容）
# 2. 升版本号：packages/xadmin-common/common/__init__.py 的 __version__（动态版本，无需重锁）
# 3. 一致性校验（版本四方 + 契约面就绪）
python scripts/release_kernel.py check
# 4. 构建 + 产物校验（wheel 含包本体 / templates/ / migrations/，元数据版本一致，sha256 留档）
python scripts/release_kernel.py build
# 5. 提交并打 tag（tag 命名 common-vX.Y.Z；发布不经 tag 自动触发）
git tag common-v0.2.0 && git push origin common-v0.2.0
# 6. 上传私有源（默认 dry-run；确认后加 --upload）——两种等价通道：
#    本地：直接跑脚本（下）；CI：Actions → Publish kernel package（workflow_dispatch 输入
#    dry-run 开关；流水线另生成 SBOM + 构建溯源并建 Release，见 §六）
XADMIN_COMMON_PUBLISH_URL=https://pypi.example.com/legacy/ \
  UV_PUBLISH_TOKEN=****** python scripts/release_kernel.py publish --upload
# 7. 私服核对：simple 索引出现 xadmin-common 新版本；宿主侧按 §四 升级
```

**发布前本地演练**（不上传、不依赖 CI；用于真发布前确认产物面与 SBOM 前提）：

```shell
python scripts/release_kernel.py check
python scripts/release_kernel.py build --out-dir /tmp/kernel-dist     # 产物 + sha256 留档
python -m zipfile -e /tmp/kernel-dist/xadmin_common-<版本>-py3-none-any.whl /tmp/kernel-unpacked/
grep -c '^Requires-Dist:' /tmp/kernel-unpacked/xadmin_common-<版本>.dist-info/METADATA
#   ↑ SBOM 步骤前提：分发包元数据带完整依赖声明（syft 据此解析依赖树）
uvx --from cyclonedx-bom cyclonedx-py requirements requirements.txt -o /tmp/sbom-server.cdx.json
#   ↑ 生成并抽查 CycloneDX 结构（bomFormat / components 数）；attestation 与 Release 只能在
#     真实 CI 跑批中验证（OIDC 签发与 secrets 不可本地复现）
```

CI 侧：`Lint Code` workflow 的 `kernel-package` job 跑 `check` + `build`（每个 PR 都验证
版本一致与产物内容）；`Publish kernel package` workflow（`workflow_dispatch`）按输入
（dry-run 开关）执行上传，凭据取仓库 secrets（缺 secret 即 fail-fast，不静默跳过）。
真上传路径另生成 CycloneDX SBOM 与构建溯源（attestation），并建带 SBOM 附件的 Release
（`prerelease`，避免污染平台发布 `releases/latest` 端点），消费方核验方式见 §六。

## 四、宿主升级与回滚

- **升级**：宿主把依赖 pin 到目标版本（如 `xadmin-common==0.2.0`）→ `uv lock` /
  `pip install -U` → 重启进程（settings 契约与内核模块在进程启动期装配）；
- **迁移**：内核模型迁移随包分发（`common/migrations/`），宿主 `manage.py migrate` 照常执行
  （迁移链对「全应用 / 全未应用」库自动兼容，口径见部署手册升级章节）；
- **回滚**：回退依赖版本并重装 + 重启进程；内核不在运行期写「版本相关状态」，回滚无需数据订正；
- **兼容矩阵**：当前仅有 xadmin-server 一个宿主，按 `>=0.2,<0.3` 消费；出现第二个宿主时
  在此登记「宿主平台版本 ↔ 内核版本」矩阵与长期支持口径。

## 五、门禁与守护

| 门禁 | 位置 | 覆盖 |
|---|---|---|
| 版本四方一致 + 契约面就绪 | `scripts/release_kernel.py check` | 源码 / 分发元数据 / changelog 首条 / 架构文档「当前」口径 |
| 产物内容校验 | `scripts/release_kernel.py build` | wheel 含包本体与 `templates/`、`migrations/`；误带宿主内容即失败 |
| 单元守护 | `tests/unit/test_kernel_release.py` | 版本一致性、changelog 形态、发布脚本纯函数、CI 口径 |
| 依赖三方一致 + 工作区骨架 | `tests/unit/test_dependency_manifest.py` | pyproject ↔ 产物 ↔ uv.lock；成员声明与 wheel 收纳面 |
| 供应链接线（SBOM / 溯源 / Release 门控） | `tests/unit/test_kernel_release.py` | 发布与镜像 workflow 的 SBOM / attestation / 权限 / `prerelease` 接线漂移 |

## 六、消费者如何验证

私有源只回答「拿到哪个版本」，不回答「这个产物是不是该仓库、该提交构建出来的」。发布流水线
为此产出两类可核验材料，宿主 / 安全审计在下游装机前应按需核验：

- **构建溯源（SLSA build provenance）**：对内核 wheel / sdist 与两个平台镜像签发 attestation
  （GitHub 可信构建证明，绑定仓库 / 提交 / 工作流 / 触发事件）；
- **SBOM（CycloneDX）**：内核包 `sbom-kernel.cdx.json`（wheel 依赖树）、平台镜像侧
  `sbom-server.cdx.json` / `sbom-client.cdx.json`（依赖审计视角），随 GitHub Release 分发。

**前置条件**：`gh` CLI ≥ 2.49（`gh attestation` 子命令）；核验需能访问签发仓库，仓库为
**private** 时 `gh attestation verify` 要求所在 GitHub 计划支持 attestations；SBOM 扫描需
`grype` / `cyclonedx` / `syft` 之一。

**1. 核验构建溯源**

```shell
# 内核包（内网离线分发同样适用：把路径换成实际拿到的产物）
gh attestation verify xadmin_common-0.2.0-py3-none-any.whl -R nineaiyu/xadmin-server
gh attestation verify xadmin_common-0.2.0.tar.gz -R nineaiyu/xadmin-server

# 容器镜像（按 digest 核验，勿用浮动 tag；digest 见 Release 说明）
gh attestation verify oci://docker.io/nineaiyu/xadmin-server@sha256:<digest> -R nineaiyu/xadmin-server
gh attestation verify oci://docker.io/nineaiyu/xadmin-client@sha256:<digest> -R nineaiyu/xadmin-client
```

核验通过会打印签名与 provenance（仓库 / 提交 / workflow / 触发 ref）；产物非本仓库出品或被
替换即失败。取镜像 digest：`docker buildx imagetools inspect nineaiyu/xadmin-server:vX.Y.Z`。

镜像溯源的取源口径（2026-10-09 定案，**保持默认，不加 `push-to-registry`**）：

- `gh attestation verify oci://…` 默认**经 GitHub API 取溯源**（`--owner` / `--repo` 定位签发
  仓库），不依赖镜像所在 registry 的 OCI 1.1 referrers 能力——当前镜像推送到 Docker Hub，
  该能力支持面无公开保证，若开启 push-to-registry 会把「registry 是否支持」变成发布链路的
  失败点；
- 需要「不访问 GitHub 也能核验」的场景才用 `--bundle-from-oci`（从 registry 取 attestation），
  它要求 `actions/attest-build-provenance` 配 `push-to-registry: true`。
- **重开条件**（任一命中再切换）：镜像迁至 GHCR；或消费方提出无 GitHub 凭据环境的离线核验
  需求（届时同步在 workflow 打开 push-to-registry 并在此登记）。

**2. 扫描依赖面（SBOM）**

```shell
# 用 grype 扫内核 SBOM 的已知漏洞（平台侧换成 sbom-server / sbom-client 附件）
grype sbom:sbom-kernel.cdx.json

# 或用 CycloneDX 工具校验 SBOM 结构并列出组件
cyclonedx validate --input-file sbom-kernel.cdx.json
```

**3. sha256 对账（传统通道）**

构建脚本在本地 / CI 日志打印 wheel 与 sdist 的 sha256（见 §三 步骤 4）。把它与 Release 附件
或私服同名产物的 sha256 比对，可确认传输未损坏：

```shell
sha256sum xadmin_common-0.2.0-py3-none-any.whl
```
