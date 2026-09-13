# ADR-021：仪表盘二期（大屏投屏）+ 报表轻量版

- 状态：已接受
- 日期：2026-09-13
- 关联：年度开发计划 2026.10-2027.09 §四 W6（G3b + G8）；ADR-020（数据集 +
  仪表盘一期，本 ADR 的直接地基）；既有下载中心（ExportRecord/UploadFile）
  与 CeleryTaskRecordModel 记账契约

## 背景

一期（ADR-020）已有数据集 + 仪表盘 + 四种图表卡片。二期补两件事：

1. **大屏投屏**（G3b）：把若干仪表盘以全屏轮播方式投到电视/大屏，定时切换
   页面并自动刷新数据；
2. **报表轻量版**（G8）：数据集 + 筛选聚合 → 定时渲染成 xlsx → 产物进下载
   中心 + 邮件送达订阅人。

## 决策

### 1. 大屏 = `Screen` 模板 + 前端全屏轮播页（后端极薄）

- `Screen` 模型：`name` / `dashboards`(JSON：仪表盘 pk 有序列表) / `interval`
  （切页秒数，默认 15）/ `refresh`（数据刷新秒数，默认 60）/ `visibility`
  （personal / shared，与 Dataset/Dashboard 同口径）。CRUD 走 BaseModelSet，
  无自定义动作；
- 前端独立路由页 `dashboard/display/index.vue`（`?pk=<screen>`）：全屏 API +
  按 interval 依次轮播屏幕内的仪表盘（每页以一期卡片网格渲染，卡片高度放大），
  每 refresh 秒触发可见卡片重新拉数（ChartCard 暴露 loadData）；深色底、隐藏
  侧边栏与导航（独立路由不在菜单树内挂载布局壳之外的部分）；
- 轮播/刷新纯前端计时器，**后端不参与轮播状态**——大屏只是仪表盘的另一种
  消费形态，权限完全复用仪表盘可见性（浏览者须对屏幕内全部仪表盘可见，
  personal 仪表盘在投屏侧对他人不可见即跳过）。

### 2. 报表 = 数据集 + 调度 + ExportRecord 产物 + 邮件附件

- `Report` 模型：`name`(唯一) / `dataset`(FK PROTECT) / `mode`(rows=行数据 /
  aggregate=聚合) / 聚合参数(`group_by`/`metric`/`date_trunc`/`value_field`) /
  `frequency`(daily / weekly / monthly) + `send_time`(HH:MM) + `weekday`
  （weekly 用，0=周一）/ `recipients`(JSON 邮箱列表，写入侧 EmailField 校验) /
  `is_active` / `last_run_at` / `last_status`；
- **调度**：单个分发器周期任务 `@register_as_period_task(crontab="5 * * * *")`
  每小时整点后 5 分钟扫描 active 报表，frequency/send_time/weekday 命中当前
  时刻即派发——不引入 croniter，不逐报表注册 beat 条目；
- **执行**：`run_scheduled_report` celery 任务（task_id = 预创建 ExportRecord.pk，
  沿用「记录 pk == task_id」契约，执行历史/增量日志零成本复用）；以**创建者**
  身份执行数据集（`get_filter_queryset`，menu 上下文为空 ⇒ 仅未绑菜单的全局
  授权生效——登记口径）；openpyxl 渲染 xlsx → 存 UploadFile → 挂 ExportRecord
  （下载中心可见可下载）；`django.core.mail.EmailMessage` 携带附件发送给
  recipients（邮件未配置/发送失败记入 record.error 与 last_status，数据产物
  不回滚）；
- **立即运行**：报表管理页 `run` 动作 = 派发一次（与周期任务同一条管线），
  用于配置自检。

### 3. 复用边界与明示不做

- 导出复用的是**产物存储与下载中心**（ExportRecord/UploadFile/执行历史），
  不复用视图重放式渲染（async_export_data_task 以 ModelSet 视图为前提，
  数据集不是 ModelSet——两种渲染并存，管线收口点不同）；
- 明示不做：报表邮件正文自定义模板（固定"报表名 + 时间 + 附件"）、按收件人
  权限分别渲染（一份产物全员同内容，权限口径=创建者）、CSV/ PDF 格式
  （一期 xlsx）、报表级细粒度定时（cron 表达式，登记候选池）。

## 测试与验收

- 单元：调度命中（daily/weekly/monthly × 时刻匹配与不匹配）、报表行/聚合
  xlsx 渲染、创建者权限上下文 fail-closed（创建者无授权 → 空产物）、邮件
  失败降级（产物 SUCCESS、error 记录）；
- 集成：Report/Screen CRUD 与越权（匿名/普通用户）、run 动作派发、recipients
  非法邮箱拒绝；
- 门禁：pytest / ruff / i18n po；前端 typecheck / eslint / locale-keys；
  E2E 主链路（建报表 → 立即运行 → 下载中心出现产物）+ 全量回归。
