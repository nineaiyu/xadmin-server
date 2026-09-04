# ADR-002: demo 应用保留但默认关闭

- 状态：已接受（2026-09-04）
- 关联：半年规划 T1.6 / TD-20

## 背景

`demo` app（Book 示例）为上游框架自带（上游提交 46caa47），用于演示 BaseModelSet / RePlusPage 的低代码开发范式。历史上其迁移文件状态反复（一度被删除后又补齐，fc09875）。当前生产路径 `config.yml` 的 `XADMIN_APPS` 为空，demo 未启用；前端 `src/views/demo/book/` 页面仍保留。仓库使用者（含本人）曾对"演示模式是否被引入"产生困惑。

## 决策

1. **保留** demo app 代码（后端 + 前端页面），它是框架开发范式的活文档与新功能集成测试的天然沙盒（tests 中 BaseModelSet 冒烟测试依赖 demo 模型，`tests/settings_test.py` 显式启用）；
2. **默认关闭**：`config_example.yml` 与文档明确 `XADMIN_APPS` 需显式声明才会加载（如 `XADMIN_APPS: [demo]`），生产环境禁止启用；
3. demo 的 migrations 必须随仓库提交（现状已满足），不允许依赖运行时 makemigrations。

## 后果

- 正面：保住框架示例与测试沙盒，消除"迁移文件要不要提交"的反复；
- 负面：仓库保留少量非生产代码——通过"默认关闭 + 文档说明"控制噪音；若未来上游 demo 与核心层耦合加深，可重新评估移除（移除需同步改 tests 的 demo 依赖）。
