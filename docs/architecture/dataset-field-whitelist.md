# 数据集模型与字段白名单（fail-closed）

> 适用范围：数据集 / 仪表盘 / 报表 / 大屏 / 动态表单的保存与执行链路。
> 事实源：`dataset/utils/dataset.py`（`available_models` / `available_fields` /
> `get_whitelisted_model` / `parse_column`）与 `system.models.ModelLabelField`。

## 一、白名单来源

| 层级 | 来源 | 说明 |
|------|------|------|
| 模型白名单 | `ModelLabelField` 中 `field_type=DATA` 且 `parent IS NULL` 的根节点 | 根节点 `name` 是模型 `app_label.model_name`（小写） |
| 字段白名单 | 对应模型根节点下的 `DATA` 子节点 | 子节点 `name` 是模型字段名；JSON 字段再按路径解析 |
| 公式 / 聚合 / 过滤 / 排序 | 同一份字段白名单 | 不允许借「过滤字段」绕过列白名单 |

白名单与权限编辑器、`sync_model_field` 同源：管理端给模型勾选字段后，数据集设计器
才能看到该模型；未登记模型不会出现在设计器元数据里。

## 二、fail-closed 口径（三处一致）

1. **保存**：`DatasetSerializer` 校验 `bound_model`、列、过滤、排序、分组字段，
   任一项不在白名单 → `ValidationError`（HTTP 400，文案 `Model/Field ... is not available for datasets`）；
2. **执行 / 聚合**：`execute_dataset` / `aggregate_dataset` / 报表 / 大屏 / 表单链路
   重新校验（不信任保存时点），同时叠加行级数据权限与浏览者字段权限；
3. **NL 查数**：`ai/services/nl_query/nl_query.py` 用同一 `get_whitelisted_model` +
   `available_fields` 闸门，LLM 输出只能落在白名单字段上（受限 DSL，不生成 SQL）。

删字段 / 下线模型后，存量数据集在下次执行时即报不可用（不会静默返回空表）；
动态表单另走 schema 版本与历史快照，不依赖本白名单。

## 三、新增模型 / 字段

```bash
# 1. 字段树登记（开发期，模型字段变更后）
python manage.py sync_model_field
# 2. 角色字段白名单补齐（新增字段默认 fail-closed，按需授权）
python manage.py sync_field_permissions --dry-run
python manage.py sync_field_permissions --update-seed   # 按团队授权口径落种子
```

> 只跑 `migrate` 不跑字段同步时，新模型/新字段对数据集不可见——这是设计行为
> （字段权限白名单制），不是缺陷。

## 四、守护测试

| 用例 | 守护语义 |
|------|----------|
| `tests/unit/dataset/test_dataset_model_whitelist.py` | 未登记模型拒绝；已登记模型只暴露白名单字段；未登记字段拒绝 |
| `tests/unit/dataset/test_dataset_columns.py` | 列解析 / JSON 路径 / 数值标注 |
| `tests/integration/dataset/test_dataset_api.py` | 保存与执行链路的字段校验 |
| `tests/integration/dataset/test_dataset_request_memo.py` | 白名单与字段权限的请求级缓存（同请求只查一次） |
| `tests/integration/dataset/test_dashboard_card_permission.py` | 卡片执行叠加字段权限（fail-closed） |

## 五、常见问题

| 现象 | 原因 / 处置 |
|------|-------------|
| 设计器里没有新模型 | `sync_model_field` 未跑 / 模型根节点不是 `DATA` 类型 / 模块被裁剪 |
| 保存成功但执行 400 | 字段树变更后未重新校验（执行侧 fail-closed），按错误文案补字段权限 |
| 报表/大屏没数据但数据集正常 | 报表引用的是个人数据集且当前用户非创建者（`visible_dataset_queryset`），或行级数据权限过滤所致 |
