# 数据模型

迁移位于 `backend/migrations/versions/`，由 Alembic 管理。MySQL 是故事、角色、分镜、任务、审批、审核、时间线、审计的唯一业务事实源。

| 领域 | 主要表 | 关键约束 |
| --- | --- | --- |
| 身份 | users, server_sessions, workspace_members, project_members | 会话可撤销；项目与工作空间双重成员校验 |
| 内容 | story_sources, characters, character_versions, episodes, scenes, shots | 角色正式版本、镜头状态与 `version` 乐观锁 |
| 生成 | generation_jobs, generation_outputs, assets, usage_records, outbox_events | 项目内幂等键唯一；Job 与 Outbox 同事务 |
| Agent | agent_runs, tool_calls, pending_actions | 待审动作唯一约束与执行状态 |
| 检索 | knowledge_documents, knowledge_versions | 文档版本与活动状态；Qdrant 仅作索引 |
| 成片 | timelines, timeline_tracks, timeline_items | 五轨时间线与最终资产引用 |
| 审计 | audit_logs | actor、资源、trace、结果与安全摘要 |

适用表同时存储 `workspace_id`、`project_id`。资源读取在 SQL 条件中限制两级 Scope。镜头、场景、角色与时间线修改使用版本号；并发预算预留对 Project 行加锁。媒体字节只存 MinIO，数据库 Asset 只存对象键、类型、哈希、大小、尺寸、时长和状态。
