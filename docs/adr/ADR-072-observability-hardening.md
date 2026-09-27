# ADR-072：日志与响应脱敏收口、配置类型契约、数据目录加固

- 日期：2026-09-27
- 状态：**已交付**（后端单测守护 + installer 脚本门禁；生产口径浏览器验收见 `ops/release-checklist.md` §2）
- 背景：三项**已登记评估出口 / 可选加固项**的集中收口（来源：`ops/observability.md` 第十轮 / 第十一轮 / 第十二轮演练登记）：

1. 第十一轮：登录中间态 `tmp_token` 落入操作日志 `body`；DEBUG 正文裁剪登记；
2. 第十轮：`convert_type` 的弱类型消费点（直接按 str 使用）存在静默接受面；
3. 第十二轮：`data` 目录 750 收紧列为可选加固项。

## 决策

### D1 日志与响应脱敏：四路同口径、递归掩码 + 截断

实施中发现**实际遗留面比登记描述更宽**——除请求体 `token` 外，操作日志的 `response_result`
会记录登录响应的 `access` / `refresh` **明文**，且 DEBUG / 慢请求日志正文此前为**未脱敏原样打印**。
收口为统一口径（`common/core/middleware.py`）：

- `desensitize_payload`：**递归**按敏感键掩码（dict / list 嵌套，`sure_password` 等复合键纳入清单），
  掩码保留长度信息；原结构不被污染；
- 四路共用：操作日志 `body`、操作日志 `response_result`（新增）、DEBUG 请求/结束日志（新增）、
  慢请求 warning（原有）；
- `log_body_preview`：脱敏**先于**截断（避免截断把敏感串留在前缀），截断上限复用
  `OPERATION_LOG_FIELD_MAX`（0 = 不落正文）；
- 审批动作的请求体快照（`approval/utils/approval/payload.py`）随之获得嵌套脱敏能力（调用点零改动）。

### D2 配置类型契约：评估结论为「无漂移」并立守护

`SysConfig` 的 66 个系统级 property 实测**零类型漂移**（返回值类型与默认值逐一一致）——
弱类型风险仅存在于「key 不在 defaults 且走环境变量」的路径，而现有 property 均已显式转换。
不改造 `convert_type`（影响面大，与登记判断一致），改为**新增守护**
`tests/unit/server/test_config_type_contract.py`：逐项断言类型契约（新增 property 自动纳入）、
property 池非空防回归、并有注入式负向验证证明守护本身能抓到漂移。

### D3 数据目录 750：installer 幂等收紧

installer `scripts/utils.sh::prepare_config` 增加幂等 `chmod 750 ${VOLUME_DIR}/server/data`：
仅调整目录位、**不递归改文件**（保留容器内既有属主/权限语义），安装 / 升级 / 配置三个调用路径
共用（与既有 config 700/600 加固同模式）；目录不存在时跳过。`scripts-check` 的 `bash -n` 门禁通过。

### D4 关联的运维收口（执行记录，非决策）

- 冷归档在生产容器完成一次真实演练（归档 → `--verify` → `--restore-range`），
  并把归档目录里 3 组历史演练残留（`module=test`，各 1 行）移出（`ops/log-archive.md` 已登记）；
- AES v1 关闭后的浏览器全流程回归完成（登录 / 改密 / 设置页），见 `ops/release-checklist.md` §2；
- 发布产物（openapi/SBOM）随版本落地经口径核对确认，本地复跑 CI 同款命令 0 已知漏洞；
- 巨型文件门禁台账口径同步（历史 17 处已清零，长期方案 §5.1 Q3）。

## 验证

- `tests/unit/common/test_operation_log_middleware.py`：递归脱敏（嵌套 dict/list、非容器直通）、
  响应快照脱敏（`access`/`refresh`/`token` 掩码且业务数据保留）、日志正文预览（脱敏后截断、上限 0 不落）；
- `tests/unit/server/test_config_type_contract.py`：类型契约逐项断言 + 注入式负向验证；
- installer：`bash -n` 语法门禁（`scripts-check` workflow）；
- 生产口径实证：浏览器验收期间的 OperationLog 记录显示 `password` / `token` / `refresh` 均已掩码
  （`release-checklist` §2 执行记录）。

## 边界

- 脱敏是**按键名**白名单（不做值形态识别）：键名不在清单内的自定义敏感字段仍需显式登记；
- 结构化 JSON 链路（AI 结构化输出、审计 diff）不做文本脱敏（与 AI 护栏口径一致）；
- 安全设置页「路由匹配但组件为空」空白为本次验收的**观察项**（与 AES 无关），已登记
  `ops/observability.md` §八 待深挖。
