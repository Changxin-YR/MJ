# RAG 与数据隔离

故事导入后经人工触发索引：MySQL 保存 KnowledgeDocument/KnowledgeVersion，Qdrant 保存文本分块与向量。每个点含 `workspace_id`、`project_id`、`status`、文档版本和来源 ID，并为 Scope 字段建 Payload Index。检索在 Qdrant 查询前加入双层 Scope 与 `ACTIVE` 过滤，不通过事后删结果实现隔离。

默认 `FakeEmbeddingProvider` 是确定性测试实现，不代表语义模型效果。设置 `EMBEDDING_MODE=dashscope` 可通过 `text-embedding-v4` 生成 1024 维真实向量；集合名包含维度及模型摘要，避免混合不同模型。真实索引与查询记录在 `evidence/final-demo/embedding.json`。固定安全测试在 Project A 查询 Project B 的唯一内容，要求返回零个 B 分块。检索文本在 Agent 上下文中标为不可信数据；权限最终由 Tool Gateway 执行。

正式版本失效/重建与独立检索质量评估尚未完成。变更模型后须对已有故事重新索引。
