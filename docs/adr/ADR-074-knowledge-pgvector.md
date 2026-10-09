# ADR-074: 知识库检索 pgvector 演进

- 状态：已交付（2026-10-02 落地，交付记录见「七」）。2026-09-29 由第二轮重构计划 7.3「AI 演进」立项为 ADR 先行；触发条件满足（语料已过 1000 线且按计划窗口提前实施）后于 2026-10 开发窗口实施。

## 一、背景

知识库向量检索当前形态（ADR-065）：

- 向量以 **float32 小端二进制** 存 `AiKnowledgeChunk.embedding`（BinaryField），随行落 PG；
- 检索 = **进程内存索引**（`ai/utils/ai_embeddings.py` 的 `vector_index()`）：每个 worker
  启动后按需把全部「新鲜向量」加载进 dict，纯 Python 余弦逐一打分（O(N×dim)）；
- 索引容量上限 `MAX_INDEXED_VECTORS = 20000`，超限**停用向量通道**（回退词频）并告警；
- 多 worker 各自维护一份内存索引（元数据签名经 `index_meta` 短 TTL 缓存止血，
  向量字节只在签名变化时回读）。

当前语料规模：约 **1200 块**（仓库文档 200+ 篇 × 分块），距容量上限尚远，
每问检索的内存余弦在毫秒级，**形态与规模匹配**。

## 二、为什么是 pgvector（而不是 Milvus / Qdrant）

对标 ruoyi-vue-pro 的 Milvus / Qdrant 多后端方案，本项目**不引入独立向量数据库**：

1. **部署形态**：本项目主推单机 compose（PG + Redis + app 三容器），新增一个
   Milvus/Qdrant（各自还要 etcd/MinIO 依赖）违背「一条 compose 拉起」的运维承诺；
   pgvector 是 PG 扩展，`CREATE EXTENSION vector` 即用，备份/恢复/HA 全部沿用
   既有 PG 体系（含每日备份脚本，零增量）。
2. **规模匹配**：10 万块以内 HNSW 索引 + 应用侧过滤绰绰有余；Milvus 的分布式
   能力在本项目语料增速（文档库，非用户上传洪峰）下是负资产。
3. **数据权限与过滤**：向量检索需要按 `source_path`/`is_active` 过滤——同库内
   SQL WHERE 直接可用，跨系统则要同步数据或双写。
4. **运维能力现状**：团队已有 PG 迁移/备份/监控全套纪律，pgvector 故障域与
   既有 DB 一致；独立向量库等于新增一条需要值班知识的组件。

## 三、目标形态（实施时照此执行）

1. **扩展与模型**：`CREATE EXTENSION IF NOT EXISTS vector`（迁移内用
   `RunSQL`，PG 专属守护——sqlite 测试环境跳过向量 DDL，沿用「测试跑词频通道」
   的既有口径）；`embedding` 二进制列迁 `VectorField(dim=...)`，维度仍以
   `embedding_dim` 显式记录（模型换档 = 旧向量陈旧跳过的既有语义不变）。
2. **索引**：`HNSW (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)`；
   写入侧维持「显式构建、幂等补缺」，不引入在线索引抖动。
3. **检索**：`search_vectors` 改为
   `ORDER BY embedding <=> query_vector LIMIT k`，RRF 融合层（`rrf_fuse`、
   权重 1.0/0.5、`TOKEN_WEIGHT` 保基线优先）**零变化**；`retrieve` 契约不变；
   向量链路任何异常仍回退词频（可用性不被向量可用性绑定的口径保留）。
4. **多 worker 一致性**：内存索引退役，天然多 worker 共享；`index_meta`
   的签名缓存仅保留给分词索引。
5. **迁移路径**：
   - ① 迁移内新建 VectorField 列（置空）；
   - ② `RunPython` 把存量 float32 二进制按行解码回填（分批，`batch_size=500`）；
   - ③ 双读窗口（内存索引实现保留一个版本，feature flag 切换）→ ④ 稳定后
     删除二进制列与内存索引代码。
6. **依赖**：`pgvector` Python 包（`pgvector.django` 提供 VectorField/操作符）；
   numpy 仍不引入——查询向量由 SDK 返回 list，`VectorField` 接受数组。

## 四、成本与风险

| 项 | 评估 |
|---|---|
| 迁移停机 | 1200 块回填秒级；10 万块约 1-2 分钟（维护窗口内） |
| 测试环境 | sqlite 不支持 pgvector——测试套件继续走词频 + 假 embedding 管线单测（float32 编解码/RRF 数学已独立成纯函数，可测性不受影响） |
| 维度变更 | 模型换档后旧向量按 `embedding_hash != content_hash or embedding_model != model` 判陈旧跳过，与现状一致；VectorField 的固定 dim 需迁移时取最近构建的维度 |
| 部署门槛 | 镜像需含 `vector` 扩展（PG 官方镜像 `pgvector/pgvector:pg18` 或自建）；installer 文档同步 |

## 五、重开条件（满足其一即立项实施）

1. **语料 > 10000 块**（现容量上限 20000 的一半——提前半档动手，不撞墙）；
2. 单问内存余弦耗时 **P95 > 50ms**（k6/负载口径），或单 worker 常驻内存因
   索引增长 > 512MB；
3. 出现**多副本横向扩展**后的向量召回不一致投诉（内存索引各自构建的固有边界，
   `docs/ops/scale-out.md` 已登记为已知边界）。

## 六、明确不做

- 不引入独立向量数据库（Milvus/Qdrant/Weaviate）——部署形态与规模都不匹配；
- 不做向量检索的多后端抽象（YAGNI：pgvector 一条路走通即止，抽象层等第二个
  真实需求出现再立）；
- 不在触发条件前「顺手迁移」——现形态与规模匹配，提前迁移只有风险没有收益。

## 七、交付记录（2026-10-02）

目标形态全量落地，与「三、目标形态」的差异与细化如下：

- **无维度列策略（对目标形态第 1 条的修正）**：`embedding_vector` 列保持**无维度**
  `vector`（而非迁 `VectorField(dim=...)`）——embedding 模型可换档，迁移期/换档窗口
  存量向量维度混存，带维度的列类型会让写入/回填直接失败。迁移
  `ai/migrations/0005_aiknowledgechunk_embedding_vector`：扩展创建（容错，pg_trgm
  同口径）→ `SeparateDatabaseAndState` 加列（DDL 幂等、扩展缺失不阻断 migrate）→
  存量二进制 500 批 `RunPython` 回填。
- **HNSW 定型助手**（`ai/utils/ai_vector_ddl.py` + `build_ai_vector_index` 命令）：
  维度稳定 → `ALTER TYPE vector(N)` + `CREATE INDEX ... hnsw vector_cosine_ops
  WITH (m=16, ef_construction=64)`；维度混存（换档窗口）→ **反向定型**撤索引退回
  无维度；<1000 行 no-op；`pg_advisory_lock` 串行化；`build_embeddings` 成功后自动
  尝试，且写库前调 `ensure_column_accepts_dim(dim)` 反向定型护栏（列定型后模型换档
  仍能写进新维度，有测试守护）。
- **检索重写**（`ai/utils/ai_embeddings.py`）：内存索引（`_INDEX`/`VectorEntry`/
  逐块余弦）退役；`search_vectors` = SQL 余弦（`CosineDistance` 升序），新鲜度
  `embedding_hash=F("content_hash")`、模型一致、**维度一致**全在 WHERE 收敛（复刻
  旧逐块 skip 语义且不触发 pgvector 维度错误）；RRF 融合层与 `retrieve` 契约零变化；
  DB 层异常一律吞掉回退词频。
- **双写回滚口径**：`build_embeddings` 向 `embedding_vector` 与二进制 `embedding`
  列**双写**（二进制列保留一个版本窗口，回滚 = 代码回退到内存索引实现，存量向量
  不丢）；`vector_index()` 保留为可用性探针（返回 `{pk: dim}`/None）。
- **依赖与镜像**：`pgvector==0.5.0`（pyproject + uv.lock + requirements 重导出）；
  PG 镜像要求更新——生产 compose / installer / CI 全部换 `pgvector` 变体
  （`registry...nineaiyu/pgvector:pg17`，测试与本地为 `pgvector/pgvector:pg17`），
  同 PG17 大版本数据目录兼容，见 `docs/ops/deployment.md`。**旧 `postgres:17`
  镜像上禁止跑 0005 迁移**（扩展缺失时 DDL 告警跳过、状态照登记，向量通道不可用）。
- **验证**：`tests/integration/ai/test_ai_vector_pg.py` 9 用例（最小门槛/维度不一致
  排除/陈旧与模型排除/余弦排序/双写/定型建索引+幂等/换档反向定型/vendor 守卫，
  DDL 用例标 `transaction=True`——ALTER TYPE 在测试事务内撞 pending trigger
  events，生产为 autocommit 无此问题）；依赖 manifest 测试 10 例绿；后端 ai 套件
  全量绿。
