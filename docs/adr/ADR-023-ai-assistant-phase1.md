# ADR-023：AI 一期——使用/二开助手（受权限约束的文档 RAG 问答）

- 状态：已接受
- 日期：2026-09-13
- 关联：年度开发计划 2026.10-2027.09 §四 W8（G4a）；G12 SysConfig 值级加密
  （本 ADR 以 AI 密钥加密先行落地其模式）；ADR-015（重依赖红线）；
  G4b AI 二期（2027-05，NL→数据集查数，本 ADR 不触生产数据是其安全前提）

## 背景

对标 jeecg AI 低代码 / 芋道 AI 模块，xadmin 的差异化打法是「受权限约束的 AI」：
一期做**使用/二开助手**——基于仓库 docs/ 知识库的 RAG 问答（站内页、回答引用
文档出处、不触生产数据）。风险条款要求供应商中立接入层与密钥值级加密。

## 决策

### 1. 供应商中立接入层 = OpenAI 兼容协议 + 可注入 http 客户端

- 一期只讲 **OpenAI 兼容 chat/completions** 协议（`POST {base_url}/chat/completions`）：
  OpenAI / DeepSeek / Qwen / Kimi / vLLM / Ollama 等主流国内外供应商与本地推理
  全部兼容，一个客户端覆盖整个市场；协议差异大的供应商（如 Anthropic 原生）
  后续按 flavor 扩展（同 ADR-018 模式）；
- 客户端收口 `common/sdk/ai/`（与 sms/im 对称）：凭据注入、http 可注入
  （单测离线）、超时与可读错误（供应商原始报文只进日志）；
- **配置走 Setting 体系**（category=`ai`）：`AI_ASSISTANT_ENABLED`（默认关）/
  `AI_BASE_URL` / `AI_API_KEY`（write_only ⇒ 值级加密落库、API 永不回传）/
  `AI_MODEL` / `AI_TIMEOUT`——**G12 值级加密模式先行落地**（LDAP bind 密钥、
  IM secret、AI API key 三处同构）；`is_enable` = 开关 AND 凭据齐全。

### 2. 知识库 = docs/ 分块入库 + 词频评分检索（一期无向量依赖）

- **同步**：`sync_ai_knowledge` 管理命令（e2e 种子/部署侧可调）扫描仓库
  `docs/**/*.md` + 根目录 README/CONTRIBUTING，按标题层级分块（## 边界，
  长块 1200 字滑动窗口），落 `AiKnowledgeChunk`（source_path / title /
  chunk_index / content / content_hash）；重复同步按 hash 幂等，只增删变更块；
- **检索**：一期用**词频重叠评分**（查询词 CJK 二元组 + ASCII 词 → chunk 词频
  加权 + 标题命中加成），Top-5 高于阈值块进上下文——零新依赖
  （rank_bm25/embedding 均不引）；召回质量不足时升级向量检索（嵌入模型走
  同一接入层第二个配置项），已登记候选池（评估出口）；
- **不触生产数据**：知识库仅来自仓库文档文件；ask 链路不查询任何业务模型。

### 3. 问答链路与权限门控

- `POST /api/system/ai/assistant/ask {question}`：菜单权限点门控（未授权
  403）；检索 → 组装提示词（系统约束：仅基于资料回答、注明出处编号、不知道
  就说不知道）→ LLM 生成 → 返回 `{answer, sources:[{title, path, chunk_index}]}`
  （sources = 实际送入上下文的块，与提示词中的编号一一对应）；
- `GET status` 动作：enabled / configured / 知识块数量 / 最近同步时间——
  前端据其渲染「未配置引导」；
- 未启用/未配置/LLM 失败 → 统一可读文案（供应商报文不回显，同 ADR-018 口径）；
- 明示不做：流式输出（一期整段返回）、多轮会话记忆（单轮问答）、AI 写操作、
  生产数据问答（G4b 边界）、提问日志审计（候选池）。

## 测试与验收

- 单元：分块（标题边界/长块切窗/hash 幂等）、检索评分（相关块排序在前）、
  LLM 客户端请求体与错误映射（stub http）；
- 集成：ask 全链路（stub LLM 返回固定答案 → 引用 sources 与检索一致）、
  未启用/未配置降级文案、越权（无菜单权限 403）、密钥加密不回显；
- 门禁：pytest / ruff / i18n po；前端 typecheck / eslint / locale-keys；
  E2E 主链路（配置页保存 + 助手页渲染 + 未配置降级文案）+ 全量回归。
