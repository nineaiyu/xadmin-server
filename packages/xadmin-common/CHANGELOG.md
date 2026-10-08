# 变更日志（xadmin-common）

> 形态遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循
> [语义化版本](https://semver.org/lang/zh-CN/)（0.x 阶段：minor = 行为 / 契约面变更，
> patch = 修复；破坏性变更允许出现在 minor，见 [发布渠道与版本策略](../../docs/ops/kernel-release.md)）。
>
> 版本单一事实源是 `common/__init__.py` 的 `__version__`；发版时先在本文件顶部登记，
> 再由 `scripts/release_kernel.py check` 校验（本文件首条版本必须等于包版本）。

## [Unreleased]

## [0.2.0] - 2026-10-08

### 变更

- **settings 读取全量改走契约访问器**：内核不再有任何裸读（`settings.KEY` /
  `getattr(settings, ...)` / 直接 import `django.conf.settings`），一律经
  `kernel_setting("KEY")`（有缺省的键，缺失回落契约缺省值）与
  `kernel_required_setting("KEY")`（必给键，缺失报 `ImproperlyConfigured` 并带用途提示）。
- **零配置缺省收敛**：此前标记「必给」的多数键按宿主出厂口径补齐内核缺省
  （验证码 / 登录锁定时长 / 上传与导出上限 / 监控阈值 / celery 与 flower 参数 /
  权限白名单与路由忽略表 / 审计 diff 等），宿主省略即按缺省工作；三层权限的数据与
  字段两个总开关因没有安全缺省而保持必给。
- `kernel_setting` 在回落缺省值时为可变容器（dict / list / set）返回副本，避免调用方
  就地改写污染契约面共享对象；命中宿主配置时原样返回（保持「就地改写 settings 生效」语义）。

### 新增

- 契约面补齐两项此前漏登记的读取键：`TRUSTED_PROXY_IPS`（可信代理地址）、
  `AUDIT_DIFF_MODELS`（字段级审计 diff 白名单），缺省均为空。
- 契约模块新增 `required_kernel_settings()`（必给键清单，宿主最小对接面同源）。
- 发布渠道：独立版本号（本文件 + `common/__init__.py`）、发布脚本
  `scripts/release_kernel.py`（check / build / publish）与
  [发布渠道与版本策略](../../docs/ops/kernel-release.md)。

## [0.1.0] - 2026-10-08

### 新增

- 首个分发包：`common`（框架内核）以 uv 工作区成员形态发布，导入名仍为 `common`；
  wheel 只收 `common` 包本体（含 `templates/` 与 `migrations/` 数据文件）。
- 内核 settings 契约面 `common/settings_contract.py`：内核读取的每个 Django settings
  键登记（类型 / 用途 / 缺省 / 消费方），附 `kernel_setting` / `kernel_required_setting`
  读取助手与静态守护测试。
