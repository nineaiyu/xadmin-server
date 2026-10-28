# ADR-098：报表设计 / 大屏布局版本化与乐观锁

- 状态：**已交付（2026-10-28）**
- 日期：2026-10-28
- 关联：[ADR-063](ADR-063-report-designer.md)（报表设计器）、
  [ADR-062](ADR-062-screen-canvas-designer.md)（大屏画布设计器）、
  [ADR-088](ADR-088-screen-canvas-toolkit-3.md)（模板版本与回滚）、
  `dataset/utils/report_design.py` / `dataset/utils/screen_layout.py`（载荷校验单一事实源）

## 一、背景

报表设计器与大屏设计器都以整包 JSON 保存：

- `Report.design`：明细列 + 行数上限 + 聚合组件；
- `Screen.layout` / `Screen.free_layout`：两种画布载荷（模式切换不互转坐标）。

保存路径是「后写覆盖」：多标签同时编辑时后保存者静默覆盖先保存者；误保存/误清空后
没有历史可回滚。大屏模板已有 `ScreenTemplateVersion`（保存即记版、可回滚），但大屏
本体与报表都没有；同时两端都缺乐观锁基线。审查登记为 REP-P1-02 / SCR-P1-02，
按 L 级版本化改造合并立项。

## 二、决策

| # | 决策 | 要点 |
|---|------|------|
| 1 | 独立版本表 | 新增 `ReportVersion` / `ScreenVersion`（`(owner, version)` 唯一），不与主表 JSON 历史混用 |
| 2 | 版本载荷最小化 | 报表只存 `design`；大屏只存 `layout` + `layout_mode` + `free_layout`。名称 / 调度 / 收件人 / 可见性 / 主题 / 轮播清单不挤占版本历史 |
| 3 | 保存即记版 | 创建时记 v1；更新时仅当版本化载荷**实质变化**才 +1（同内容保存不产生噪声版本） |
| 4 | 保留窗口 | 每行保留最近 `REPORT_VERSION_KEEP` / `SCREEN_VERSION_KEEP`（缺省 20，可用 settings 覆盖），超出裁剪最旧 |
| 5 | 回滚可逆 | 回滚恢复历史载荷并**再记一版**（不删除历史），回滚本身可再次回滚 |
| 6 | 乐观锁用版本号 | 写请求可携带 `base_version`（客户端所见 `design_version` / `layout_version`）；与当前版本不一致即 400 拒绝后写覆盖，未携带基线的历史客户端保持原语义 |
| 7 | 归属口径 | `versions` / `rollback` 与编辑同口径：仅创建者或超管（非创建者 1003）；版本历史不对外共享 |
| 8 | 权限点入种子 | 新增 `versions:DataReport` / `rollback:DataReport` / `versions:DataScreen` / `rollback:DataScreen`，路径与种子同源（未登记 = 非超管 403） |

## 三、实现落点

- 模型：`dataset/models/report.py::ReportVersion`、`dataset/models/screen.py::ScreenVersion`；
  主表增 `design_version` / `layout_version`（默认 1，随版本表同步，不因版本记账改动 `updated_time`）；
- 服务：`dataset/services/report.py::snapshot_report_version`、
  `dataset/services/screen.py::snapshot_screen_version`（快照 + 保留裁剪）；
- 视图：`ReportViewSet` / `ScreenViewSet` 的 `perform_create` / `perform_update` 记版；
  `GET {pk}/versions` 返回最新在前；`POST {pk}/rollback` 行锁 + 直接还原已验证的历史载荷；
- 序列化：`base_version`（write_only）校验；`design_version` / `layout_version` 只读下发；
- 前端：设计器保存带 `base_version`，成功后回写服务端版本；历史版本弹窗（`VersionHistoryDialog`）
  选择回滚，报表回填设计、大屏重新装载两份载荷并清选中；
- 迁移：`dataset/migrations/0003_report_screen_versions.py`。

## 四、边界与取舍

- **只保护设计/布局写面**：改名、调度、收件人等列表表单编辑不携带 `base_version`，
  不会因设计版本落后被拒；并发改这些字段仍是后写覆盖（不在本 ADR 范围）。
- **大屏回滚同时还原两份载荷**：只还原当前模式的载荷会让另一模式的历史布局丢失；
  回滚直接使用已通过校验的快照，不再二次规范化（历史快照不可变）。
- **乐观锁用整数版本而非 `updated_time`**：项目 `DATETIME_FORMAT` 为秒级，同一秒内两次
  保存无法区分先后；版本号单调递增，语义更明确。
- **历史仅作者可见**：与团队模板版本一致，避免共享资源的历史布局被低权用户读取。
