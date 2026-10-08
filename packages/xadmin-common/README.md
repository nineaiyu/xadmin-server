# xadmin-common —— 框架内核独立分发包

> **是什么**：`xadmin-server` 仓中的框架内核（`common`）以 uv 工作区成员形态发布的分发包。
> 宿主（xadmin-server 或二开项目）依赖本包后，导入名仍是 `common`
> （**分发名 `xadmin-common` ↔ 导入包 `common`**）。
>
> **包内文档**：内核的目录地图、边界规则与扩展纪律见 [common/README.md](common/README.md)
> （随包分发，是本包的主要文档）。

## 一、安装与接线

```shell
uv add xadmin-common                 # 或 pip install xadmin-common
uv add "xadmin-common[storage]"      # 启用对象存储后端（django-storages + boto3）
```

宿主侧三件事：

1. `INSTALLED_APPS` 加入 `"common"`（`CommonConfig.ready()` 负责信号、周期任务与契约装配）；
2. 按 [settings 契约](common/settings_contract.py)提供配置——**有缺省值的键缺失即按默认值工作，
   标记「必给」的键必须提供**；契约表见
   [《框架内核独立分发包》§三](../../docs/architecture/kernel-package.md)；
3. 需要宿主业务能力（菜单、系统配置等）时，由宿主 app 在 `ready()` 注册契约提供方，
   或在 `xadmin.contracts` entry point 装配（见 `common/contracts.py`）。

## 二、目录与构建

```
packages/xadmin-common/
├── pyproject.toml     # 分发元数据（本文件同目录）
├── README.md          # 本文件（分发视角）
└── common/            # 框架内核源码（导入包名 = common）
```

```shell
uv build --package xadmin-common            # wheel + sdist → dist/
uv run --package xadmin-common ...          # 在成员项目上下文执行
```

工作区内的其它项目（`xadmin-server` 本身、后续拆分出的服务）经根
`pyproject.toml` 的 `[tool.uv.workspace]` 以 **editable** 方式消费本包：
改 `common/` 源码即时生效，无需重装。

## 三、边界（改内核前必读）

- 内核**禁止** import 任何业务 app 与 `server`（工程层）——由
  `scripts/check_cross_app_imports.py` 扫描本目录强制；
- 内核消费宿主能力的唯一出口是 `common/contracts.py`（声明式契约面 + 注入制）；
- 内核读取的 Django settings 全部登记在 `common/settings_contract.py`，由
  `tests/unit/common/test_settings_contract.py` 静态守护（新增读取必须同步登记）；
- 内核变更影响全部宿主，提交前跑全量 pytest 而不是只跑改动面。
