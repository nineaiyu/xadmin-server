#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""内置种子装配的数据结构：文件描述、清单、装载计划、问题项。

平台层（``loadjson/``）与各 app 的 ``seeds/`` 共用同一套描述。装载（``load_init_json``）
与导出（``dump_init_json``）都从同一份 :class:`SeedPlan` 出发，避免两侧各写一套归属逻辑。
"""

from dataclasses import dataclass, field
from pathlib import Path

# 平台共享层标识：不属于任何 app 的种子（目录菜单、全局配置、通用字典、种子角色）
PLATFORM = "__platform__"

# 授权声明文件（``grants.json``）：不是 fixture，装载后按「角色 → 菜单名」并集应用。
# 用哨兵模型名与数据文件区分，避免校验阶段把它当 loaddata 输入。
GRANTS_MODEL = "seed.grants"

# 种子类别
KIND_CONTRACT = "contract"  # 功能契约：不装即不可用（权限点 / 默认配置 / 内置流程）
KIND_GENERATED = "generated"  # 由代码推导后回写的生成物（模型字段树），禁止人工编辑
KIND_SAMPLE = "sample"  # 示例数据：不装只是没有示例，不影响功能
KINDS = (KIND_CONTRACT, KIND_GENERATED, KIND_SAMPLE)

# 问题级别
LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"

# 装配说明类别：决定命令行级别与计数口径。
# - conflict：自然键被库内占用，整行跳过（必须 WARNING + 计入「跳过」数）；
# - prune：被跳过行的引用从保留行里移除（数据被改写，同样 WARNING）；
# - strip：覆盖式 m2m 字段被剥离，行照常导入（策略行为，不是冲突）。
NOTE_CONFLICT = "conflict"
NOTE_PRUNE = "prune"
NOTE_STRIP = "strip"


class SeedNote(str):
    """装配说明：字符串兼容，附带类别 ``kind``。

    历史上所有装配说明都被命令层当作「种子冲突」打印，新装环境因此出现 4 条
    「剥离 menu」WARNING 和一句「共跳过 4 处」——实际一行未跳过。继承 ``str``
    是为了不破坏既有调用方（测试、二开脚本按字符串比较/拼接）；``kind`` 让命令
    层按类别决定级别与计数。未标类别的普通字符串按 ``conflict`` 处理：宁可多报，
    不可把真冲突降级成提示。
    """

    kind: str

    def __new__(cls, message: str, kind: str = NOTE_CONFLICT) -> "SeedNote":
        obj = super().__new__(cls, message)
        obj.kind = kind
        return obj


@dataclass(frozen=True)
class SeedFileSpec:
    """一个种子文件：属于哪一层、对应哪个模型、按什么顺序装载。"""

    app: str
    model: str
    path: Path
    kind: str = KIND_CONTRACT
    order: int = 10

    @property
    def is_platform(self) -> bool:
        return self.app == PLATFORM

    @property
    def name(self) -> str:
        """文件名（清单里声明的那一段相对路径）。"""

        return self.path.name


@dataclass(frozen=True)
class SeedManifest:
    """一个种子层（平台层或某个 app）的声明。

    ``files`` 是契约种子（含生成物），``sample`` 是示例种子。``depends`` 声明装载依赖，
    装载器据此做拓扑排序；``url_prefixes`` / ``menu_roots`` / ``config_keys`` / ``dict_codes``
    是归属声明的输入面，供归属校验使用。
    """

    app: str
    depends: tuple[str, ...] = ()
    url_prefixes: tuple[str, ...] = ()
    menu_roots: tuple[str, ...] = ()
    config_keys: tuple[str, ...] = ()
    dict_codes: tuple[str, ...] = ()
    files: tuple[SeedFileSpec, ...] = ()
    sample: tuple[SeedFileSpec, ...] = ()
    grants: SeedFileSpec | None = None


@dataclass(frozen=True)
class SeedPlan:
    """一次装载 / 导出的完整计划（已按依赖与文件内顺序排好）。"""

    platform: tuple[SeedFileSpec, ...] = ()
    apps: tuple[str, ...] = ()
    files: dict[str, tuple[SeedFileSpec, ...]] = field(default_factory=dict)
    samples: tuple[SeedFileSpec, ...] = ()
    grants: tuple[SeedFileSpec, ...] = ()
    notes: tuple[str, ...] = ()

    def all_specs(self) -> list[SeedFileSpec]:
        """按装载顺序返回全部文件：平台层 → 各 app（拓扑序）。"""

        specs = list(self.platform)
        for app in self.apps:
            specs.extend(self.files.get(app, ()))
        return specs

    def model_labels(self) -> list[str]:
        """计划覆盖的模型（去重保序）：供时间戳回填与装载后校验使用。"""

        labels: list[str] = []
        for spec in self.all_specs():
            if spec.model not in labels:
                labels.append(spec.model)
        for spec in self.samples:
            if spec.model not in labels:
                labels.append(spec.model)
        return labels


@dataclass(frozen=True)
class Problem:
    """校验问题：级别 + 稳定错误码 + 可定位的文件与行号。"""

    level: str
    code: str
    message: str
    file: Path | None = None
    row_index: int | None = None

    def __str__(self) -> str:
        location = str(self.file) if self.file is not None else "-"
        if self.row_index is not None:
            location = f"{location}#{self.row_index}"
        return f"[{self.level}] {self.code} {location}: {self.message}"


@dataclass
class LoadReport:
    """装载准备结果：交给 ``loaddata`` 的文件清单与装配说明。

    ``notes`` 保留全部说明（兼容既有调用方）；命令层按类别取用：
    ``conflict_notes`` / ``prune_notes`` 是必须让用户看见的数据让位，
    ``strip_notes`` 是行照常导入的策略性字段剥离。
    """

    fixture_labels: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    trimmed: bool = False

    def notes_of(self, kind: str) -> list[str]:
        """按类别筛选说明；未标类别的普通字符串按 ``conflict`` 处理。"""

        return [note for note in self.notes if getattr(note, "kind", NOTE_CONFLICT) == kind]

    @property
    def conflict_notes(self) -> list[str]:
        """与库内数据冲突、整行被跳过的说明。"""

        return self.notes_of(NOTE_CONFLICT)

    @property
    def prune_notes(self) -> list[str]:
        """被跳过行的引用从保留行中移除的说明（行本身仍导入）。"""

        return self.notes_of(NOTE_PRUNE)

    @property
    def strip_notes(self) -> list[str]:
        """策略性剥离覆盖式 m2m 字段的说明（行照常导入）。"""

        return self.notes_of(NOTE_STRIP)


class SeedPlanError(ValueError):
    """计划阶段错误（未知 app、依赖缺失、依赖成环）——由命令层转为可读报错。"""
