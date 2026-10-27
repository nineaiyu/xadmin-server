# 字段类型矩阵（Django 字段 → 四通道渲染）

> 二开选型速查：**模型里写什么字段，前端得到什么渲染**。本页是
> [metadata-protocol.md](metadata-protocol.md) §三的字段类型全量展开，两者配合阅读：
> 协议页讲机制，本页讲“每个类型在哪条通道、显示成什么、怎么校验”。
>
> 事实源：`packages/xadmin-common/common/core/modelset/input_types.py::DECLARED_INPUT_TYPES`
> （词表）与 `common/drf/metadata.py::get_field_type`（类型判定）。词表变更走
> [framework-cookbook.md](framework-cookbook.md) §六五步清单。
>
> **活样本**：`demo.Book` 的 `contact_phone` / `theme_color` / `introduction` 分别演示
> `phone` / `color`（含 `predefine`）/ `rich_text`，可直接对照 `/api/demo/book/search-columns`
> 的载荷与页面渲染。

## 一、第一跳：Django 模型字段 → DRF 字段 → `input_type`

二开在模型里声明 Django 字段，`ModelSerializer` 按 DRF `serializer_field_mapping`
转成序列化器字段，元数据再经 `get_field_type` 推断 `input_type`。**用量**为本仓业务
app 模型字段声明统计，用于判断该类型的真实出镜率。

### 1.1 数值 / 自增

| Django 模型字段 | DRF 字段 | `input_type` | 用量 | 前端归宿 |
|---|---|---|---|---|
| `AutoField` / `BigAutoField` | Integer / BigInteger | `integer` | 2 | ✅ input-number（通常只读主键） |
| `IntegerField` / `SmallIntegerField` | IntegerField | `integer` | 64 | ✅ input-number（min/max 随元数据） |
| `PositiveIntegerField` / `PositiveSmallIntegerField` | IntegerField | `integer` | 11 | ✅ input-number（`min_value=0` 由 DRF 下发） |
| `BigIntegerField` | BigIntegerField | `integer` | 3 | ✅ |
| `FloatField` | FloatField | `float` | 15 | ✅ input-number（min/max/step） |
| `DecimalField` | DecimalField | `decimal` | 1 | ✅ input-number（`decimal_places` → step/precision） |
| `DurationField` | DurationField | `duration`（未登记） | 0 | ❌ 闭包拦截；0 消费，命中再立项 |

### 1.2 文本 / 复合

| Django 模型字段 | DRF 字段 | `input_type` | 用量 | 前端归宿 |
|---|---|---|---|---|
| `CharField` | CharField | `string` | 259 | ✅ input |
| `TextField` | CharField + `style=textarea` | `textarea`（自动） | 20 | ✅ autosize 文本域（零声明自动） |
| `EmailField` | EmailField | `email` | 1 | 回退 input + 格式 rule |
| `URLField` | URLField | `url` | 2 | ✅ input + URL rule；详情/列表可点击链接 |
| `SlugField` | SlugField | `slug`（未登记） | 0 | ❌ 闭包拦截 |
| `UUIDField` | UUIDField | `string` | 22 | ✅ input（多为主键/外键只读） |
| `BinaryField` | `ModelField` 兜底 | `field` | 2 | ⚠️ 兜底 input（两处均未暴露进序列化器） |
| `GenericIPAddressField` | IPAddressField | `string` | 2 | ✅ input（审计日志；如需展示增强见候选 `ip`） |
| `JSONField` / postgres `JSONField` / `HStoreField` | JSONField | `json` | 68 | ✅ JsonInput / vue-json-pretty |
| postgres `ArrayField` | ListField | `list` | 0 | ✅ 多选 / TagInput |
| `FilePathField` | FilePathField（ChoiceField 子类） | `choice`（自动） | 0 | ✅ select |

### 1.3 布尔 / 日期 / 选择

| Django 模型字段 | DRF 字段 | `input_type` | 用量 | 前端归宿 |
|---|---|---|---|---|
| `BooleanField` / `NullBooleanField` | BooleanField | `boolean` | 61 | ✅ 分段单选 |
| `DateField` | DateField | `date` | 2 | ✅ date-picker |
| `DateTimeField` | DateTimeField | `datetime` | 36 | ✅ date-picker + 列表格式化 |
| `TimeField` | TimeField | `time` | 2 | ✅ time-picker |

任意上述字段带 `choices=` 时，DRF 改产 `ChoiceField` → `choice` → select 下拉；配合
`DictChoiceField`（字典驱动）或 `input_wrapper` 可进一步定制。

### 1.4 关联 / 文件

| Django 模型字段 | DRF 字段 | `input_type` | 用量 | 前端归宿 |
|---|---|---|---|---|
| `ForeignKey` / `OneToOneField` | `BasePrimaryKeyRelatedField` | `object_related_field` | 72 | ✅ 全通道（候选截断 / api-search-* / suggest_url） |
| `ManyToManyField` | `ManyRelatedField` | `m2m_related_field` | 20 | ✅ |
| 附件外键（→ `file.UploadFile`） | 关联字段 + 后缀合成 | `*_related_field_file` / `_image` | — | ✅ 上传 / 图片组件 |
| `FileField` / `ImageField` | FileField / ImageField | `file upload` / `image upload` | 1 / 0 | ✅ UploadFile / 图片预览 |

**兜底**：未映射的自定义模型字段 → DRF `ModelField` → `field` → 回退 input（可编辑、
无类型语义）。自定义模型字段应同步声明序列化器字段类或 `input_type`。

## 二、词表逐项矩阵（`input_type` 视角）

通道标记：✅ 内置渲染器；「回退」= 注册表回退语义；「—」= 该通道本不该出现
（搜索侧由 filterset widget 面另行产出）。

### 2.1 基础标量

| `input_type` | 后端来源 | 值形态 | 搜索 | 表单 | 详情 | 列表 | rule | 高级筛选 |
|---|---|---|---|---|---|---|---|---|
| `string` | CharField / UUIDField / IPAddressField | 标量 | 回退 | ✅ input | 回退文本 | 回退文本 | required | text |
| `field` | DRF 兜底 | 标量 | — | ✅ input | 回退 | 回退 | required | 自由文本 |
| `integer` | IntegerField | 数值 | — | ✅ input-number | 回退 | 回退 | ✅ 非数字拒绝 | number |
| `float` | FloatField | 数值 | — | ✅ input-number | 回退 | 回退 | required | number |
| `decimal` | DecimalField | 数值（DRF 序列化为字符串） | — | ✅ input-number（step/precision） | 回退 | 回退 | ✅ 小数格式 | number |
| `boolean` | BooleanField | 布尔 | — | ✅ 分段单选 | ✅ options | — | required | boolean |
| `date` / `datetime` / `time` | DRF 日期族 | 日期字符串 | datetime ✅ | ✅ picker | 回退 | ✅ | required | date/datetime/time |
| `email` | EmailField | 标量 | 回退 | 回退 | 回退 | 回退 | ✅ 格式 | text |
| `url` | URLField | 标量 | 回退 | ✅ input | ✅ 超链接 | ✅ 超链接 | ✅ URL 格式 | text |
| `textarea` | CharField + `base_template` | 长文本 | — | ✅ autosize | 回退 | 回退 | required | text |

### 2.2 选择 / 字典

值形态 `{value,label,color?}` 是取值口径锚点。

| `input_type` | 后端来源 | 值形态 | 搜索 | 表单 | 详情 | 列表 | rule | 高级筛选 |
|---|---|---|---|---|---|---|---|---|
| `choice` / `multiple choice` | ChoiceField 族 | 标量 / 数组 | — | ✅ select | 回退 | 回退 | required | choice |
| `labeled_choice` | LabeledChoiceField / DictChoiceField | `{value,label,color?}` | — | ✅ select | ✅ 彩色 tag | ✅ 彩色 tag | required | choice |
| `labeled_multiple_choice` | LabeledMultipleChoiceField | `[{value,label}]` | — | ✅ 多选 | ✅ 标签列 | ✅ 标签列 | required | choice |

### 2.3 关联 / 文件

| `input_type` | 值形态 | 搜索 | 表单 | 详情 | 列表 | 高级筛选 |
|---|---|---|---|---|---|---|
| `object_related_field` | `{pk,label,…}` | api-* / suggest | ✅ select | ✅ | ✅ | choice |
| `m2m_related_field` | `[{pk,label}]` | api-* / suggest | ✅ 多选 | ✅ 标签列 | ✅ 标签列 | choice |
| `object_related_field_file` / `_image` | 文件对象 | — | ✅ 上传 | ✅ 链接 / 图片 | ✅ | choice |
| `m2m_related_field_file` / `_image` | 文件对象数组 | — | ✅ 上传 | ✅ 链接 / 图片 | ✅ | choice |
| `api-search-*`（开放前缀） | 组件自定义 | ✅ | ✅ | 映射为关联族 | ✅ | — |

### 2.4 复合 / 专用 / 搜索侧

| `input_type` | 后端来源 | 值形态 | 表单 | 详情 | 列表 | 备注 |
|---|---|---|---|---|---|---|
| `json` | JSONField / DictField / 嵌套 Serializer | 任意 JSON | ✅ JsonInput | ✅ pretty | ✅ stringify | 嵌套序列化器按 JSON 呈现（见 §三） |
| `list` | ListField | 数组 | ✅ 多选 / TagInput | ✅ stringify | ✅ | child.choices 下发时自动多选 |
| `phone` | `PhoneField` | `{code,phone}` | ✅ PhoneInput（国码） | ✅ `+86 …` + 复制 | ✅ | 服务端 E.164 + 格式校验 |
| `color` | `ColorField` | 色值字符串 | ✅ color-picker（predefine） | ✅ 色块 + 色值 | ✅ | 服务端色值格式校验 |
| `rich_text` | `RichTextField` | HTML 字符串 | ✅ WangEditor | ✅ 净化后 HTML | ✅ 去标签摘要 | 入库 bleach + 展示 DOMPurify |
| `password` | `PasswordField` / 显式声明 | 字符串（write_only） | ✅ 遮蔽输入 | —（不出现） | —（不出现） | 通用 required；强度策略由消费方运行时追加（`passwordPolicy.ts`） |
| `text` / `number` / `select` / `select-multiple` / `datetimerange` / `select-ordering` / `input` | filterset widget 面 | 标量 / 数组 | ✅ | — | — | 仅 `search-fields` 通道 |

## 三、DRF 可达长尾与处置

`SimpleMetadata.label_lookup` 可达但词表未收的类型，按“不预登记、出现即被闭包测试
拦截”处理：

| DRF 类型 | `label_lookup` | 现状态 | 处置 |
|---|---|---|---|
| `URLField` | `url` | 已登记（2 消费点） | 表单 input + URL rule、详情/列表超链接 |
| `DecimalField` | `decimal` | 已登记（1 消费点） | 表单 input-number（step/precision）、数值 rule |
| 嵌套 `Serializer` / `ListSerializer` | `nested object` | 已转 `json` | 元数据协议**不支持嵌套表单**；按 JSON 只读展示 / JsonInput 写入，语义由业务序列化器负责 |
| `DurationField` | `duration` | 0 消费 | 暂不登记；命中再立项 |
| `RegexField` / `SlugField` | `regex` / `slug` | 0 消费 | 暂不登记 |

## 四、候选新增（按价值排序）

| 类型 | 值形态 | 试点场景 | 优先级 |
|---|---|---|---|
| `ip`（独立词表项） | 标量 | 审计日志、IP 白名单 | P2（有消费再登记） |
| 表单侧成对日期 `daterange` | `[start,end]` | 暂无强消费 | 候选池 |
| `cron` | 表达式 | 周期任务（当前手写表单） | 候选池 |
| `rating` / `slider` / `icon` | 标量 | 无真实需求 | 候选池（不预做） |

## 五、扩展声明面（现有 6 条）

1. **字段类自带**：`PhoneField` / `ColorField` / `RichTextField` / `PasswordField` /
   `StepFloatField` / `LabeledChoiceField` / `DictChoiceField`；
2. **`input_wrapper(字段)(input_type=...)`**：给任意字段（含 SerializerMethodField）指定渲染类型；
3. **前 / 后缀修饰**：`input_type_prefix` / `input_type_suffix`（上传关联 `_file` / `_image` 即此机制）；
4. **`style.base_template = "textarea.html"`**：CharField 长文本 → `textarea`；
5. **`api-search-*` + `SuggestionsAction`**：大数据量关联远程联想（`suggest_url`）；
6. **页面级逃生舱 `*Format`**：`listColumnsFormat` / `addOrEditOptions` 对单列覆写（不进词表）。

扩展属性面保持最小集：`min_value` / `max_value`（DRF 原生）、`step`（StepFloatField）、
`decimal_places`（DecimalField）、`predefine`（ColorField）。

## 六、校验归属

| 层 | 负责什么 | 入口 |
|---|---|---|
| 序列化器字段类 | 类型 / 格式 / 空值语义（服务端最终裁决） | `common/core/fields.py`、`validators.py` |
| 元数据 rule | 前端提交前的即时反馈（与后端同口径，不替代后端） | `RePlusPage/src/utils/fieldTypeRegistry.ts` + `src/utils/rules/` |
| 字段权限 / 脱敏 | 字段是否下发、值是否脱敏 | 见 [permission.md](permission.md) |

## 七、守护与门禁

- 服务端词表闭包 + Schema 校验：`tests/unit/common/test_metadata_schema.py` 递归遍历
  URLconf，**全部**带 `search-columns` / `search-fields` 动作的视图集都在闭包内，
  且真实载荷必须同时满足两份 Schema（含 `child` / `predefine` / `decimal_places` 键形状）；
- Schema 锁步：两份 `docs/schema/*.schema.json` 的 `input_type` 枚举 ≡ 词表；
- 前端词表 ⇄ 注册表：`RePlusPage/__tests__/metadata-vocabulary.spec.ts`；
- 四通道成对：`renderers-pairing.spec.ts`（详情/列表成对、表单→详情反向、搜索键分类）；
- 能力清单：`RePlusPage/__tests__/fieldTypeRegistry.spec.ts`（rule / filterKind / 渲染投影）。
