# FrameForge 验收证据矩阵

更新于 2026-09-26。状态只反映已执行的测试与运行证据；`PASS` 表示该行列出的范围已验证，不表示整个冻结设计已完成。

| 要求 | 实现 | 测试或验证 | 运行证据 | 状态 |
| --- | --- | --- | --- | --- |
| MySQL 事实源、迁移、Compose | `backend/app/models.py`、`backend/alembic/`、`docker-compose.yml` | `alembic upgrade head`、`alembic check` | MySQL/Redis/Qdrant/MinIO/API/Worker/Dispatcher/Frontend 已启动 | PASS |
| 身份、角色、项目 Scope | `app/auth/`、`app/workspace/` | `test_core_flow.py`、`test_security_roles.py` | Viewer 生成 403，Editor 编辑 LOCKED 镜头 409，外部用户读取项目 404 | PASS |
| 乐观锁、镜头状态、角色版本 | `app/storyboard/`、`app/character/` | `test_core_flow.py`、`test_state.py` | 版本冲突与状态转换测试通过 | PASS |
| 中文 RAG、对白上下文与项目隔离 | `app/rag/service.py`、`app/providers/embeddings.py` | `test_rag_scope.py`、`test_provider_contract.py` | 语义块约 680 字符；连续对白块约 420 字符并保留 6 个语义单元重叠；同轮 `dialogue_run_id` 邻块恢复、开头 anchor、显式说话人线索、无主体对白标记、角色名辅助识别、对白定向多查询召回与轻量重排均有回归测试；普通叙事查询和不同对白轮次不会被错误合并 | PASS（自动化） |
| 真实向量与语义检索 | `app/providers/embeddings.py`、`app/rag/service.py` | `scripts/embedding_smoke.py` | 历史 [embedding.json](../evidence/final-demo/embedding.json)：`text-embedding-v4`，1024 维，真实查询返回本项目来源；当前 smoke 新增中文连续对白 probe，会比较目标“钥匙对白”和无关“药田对白”的余弦相似度，并验证无主体对白上下文。该新增 probe 尚未重新调用付费真实模型执行 | PASS（历史真实样本 + 当前自动化；新增真实对白 probe 待重跑） |
| Director、Tool Gateway、审批与撤销 | `app/agent/` | `test_director.py`、`test_prompt_injection.py` | PendingAction 审批后执行；会话撤销后 resume 被拒；恶意故事内容无法触发危险工具 | PASS |
| 真实 Director 文本分析 | `app/providers/llm.py`、`app/agent/workflow.py` | `scripts/director_llm_smoke.py` | [director-llm.json](../evidence/final-demo/director-llm.json)：Qwen Plus、真实 RAG、只读工具、审计 Trace | PASS（只读 ANALYZE） |
| MCP Adapter | `app/mcp/server.py` | `test_mcp_adapter.py`、`scripts/mcp_smoke.py` | [mcp.json](../evidence/final-demo/mcp.json)：实际 stdio 协议发现 3 个只读工具并读取本项目上下文；跨会话调用测试拒绝 | PASS（本地只读适配器） |
| Job、Outbox、幂等、预算 | `app/generation/`、`app/workers/` | `test_generation_flow.py`、`test_budget.py` | Redis 暂时故障时 MySQL Job 留存；同一幂等键返回同一 Job；预算拒绝超限请求 | PASS（现有测试范围） |
| 真实图像、视频、配音 Provider | `app/providers/dashscope.py` | `scripts/provider_smoke.py`、`scripts/integrated_real_smoke.py` | [provider report](../evidence/real-provider/report.json)、[integrated report](../evidence/integrated-real/report.json)：Wan T2I、Wan I2V、Qwen TTS | PASS |
| 文件上传与隔离 | `app/asset/` | `test_upload.py` | 隔离前缀签名上传、Finalize 验证后 READY；新增服务端直传同样执行媒体校验并支持时间线 WAV；非法素材被拒，素材库不会尝试预览 QUARANTINED 对象 | PASS |
| Inspector、中文硬门禁与人工复核 | `app/agent/inspector.py`、`app/storyboard/state.py` | `test_provider_contract.py`、`test_generation_flow.py`、`test_timeline_flow.py` | 当前代码对视频抽取 4 个时间点联合检查；非中文可见文字 FAIL 不允许人工 override，历史 override 也不能进入时间线。旧 [inspection.json](../evidence/final-demo/inspection.json) 的 12/12 真实视频结果来自此前真实复检，尚未按新四帧策略重新产生付费模型证据 | PASS（新策略自动化；真实证据待重跑） |
| 60 秒真实成片、五轨时间线与字幕 | `app/timeline/`、`scripts/final_demo.py` | `test_timeline_flow.py`、`scripts/rerender_demo.py` | 历史真实成片 [final.mp4](../evidence/final-demo/final.mp4) 为 60.000 秒、1280×720、H.264/AAC；当前代码已补齐 MUSIC/SFX WAV 上传、保留、删除与 FFmpeg 混音，并由 CI 使用真实 WAV/FFmpeg 路径验证。历史 60 秒成片尚未加入新的 MUSIC/SFX 后重新生成 | PASS（旧真实成片 + 新五轨自动化） |
| 资产血缘与审计 | `app/asset/routes.py`、`app/audit/` | Final Demo 脚本 | [lineage.json](../evidence/final-demo/lineage.json)；初次全链路报告记录 146 条审计事件 | PASS |
| 前端构建与核心 E2E | `frontend/src/`、`frontend/e2e/` | `npm run build`、`npm run e2e` | GitHub Actions run #202：Vue production build 通过；注册→角色激活→角色绑定分镜→生成→审核→时间线→播放成片，以及 Director→PendingAction→Approve→Resume 两条浏览器流程通过 | PASS（2 条 E2E） |
| ComfyUI 私有实例与安全适配器 | `app/providers/comfyui.py`、`ops/comfyui/`、`scripts/start-comfyui-local.ps1` | `test_comfyui_adapter.py`、`scripts/comfyui_smoke.py`、`scripts/comfyui_job_smoke.py` | [适配器实图](../evidence/comfyui-live/sample.png)、[实图报告](../evidence/comfyui-live/report.json)、[完整 Job 链路](../evidence/comfyui-live/job-report.json)：RTX 4060 上 1024×576 实图，API→Outbox→Worker→ComfyUI→MinIO 成功；可选 Compose 镜像尚未完成运行验证 | PASS（本机私有实例） |
| Provider Callback 防重放 | `app/generation/callbacks.py`、`provider_callback_receipts` 迁移 | `test_provider_callback.py` | HMAC、时间窗、持久化 nonce、Provider/remote job/状态绑定测试通过；合法回调只写 Outbox 唤醒 Worker，不直接完成任务；原生 DashScope 仍由轮询处理 | PASS（受信任中继协议） |
| Agent Eval 固定数据集与真实回答 | `backend/evals/cases.json`、`backend/tests/test_agent_eval.py`、`scripts/director_quality_eval.py` | `scripts/agent_eval_report.py`、真实 Qwen Plus + RAG 三问 | [规则报告](../evidence/agent-eval/report.json)：7/7；[真实回答](../evidence/agent-eval/open-quality.json)：信号钥匙、截止时间、寄件人姓名三问 3/3，均有项目来源且无生成副作用 | PASS（所列十个样本） |
| 安全、并发与故障矩阵 | `backend/tests/` | 权限/Scope、中文 RAG、长连续对白跨块恢复、无主体对白、角色说话人线索、对白轮次隔离、第一人称代词防误标、并发预算与编号、唯一 Timeline/KnowledgeDocument、工具权限、上传、Prompt Injection、Callback Replay、FFmpeg 注入、幂等、Provider 熔断与临时/永久失败、语言硬门禁、派生素材失效 | GitHub Actions run #202：59 个后端测试通过；Ruff 与 `alembic check` 通过 | PASS |
| Playwright Flow C | `frontend/src/views/project/DirectorPanel.vue` | `frontend/e2e/director-approval.spec.ts` | Director 提交生成请求，待审批后批准并继续，生成任务出现 | PASS |

## 真实成片说明

完整纵向流程产生 12 张真实图、12 段真实视频、12 段真实配音与一条 60 秒成片。初次报告 [report.json](../evidence/final-demo/report.json) 记录生成和审计阶段。随后依据视觉检查修正四个镜头，并为钟楼造型连续性再修正一个镜头；最新成片资产 ID 与媒体参数以 [rerender-report.json](../evidence/final-demo/rerender-report.json) 为准。

初次报告的 `inspection` 列表是当时配音任务的技术检查，不代表视觉检查。该覆盖错误已修正。历史视频资产的 Qwen VL 结果以 [inspection.json](../evidence/final-demo/inspection.json) 为准，12/12 PASS；本轮代码把视频检查升级为四时间点抽帧并把非中文可见文字变成不可 override 的硬门禁，但没有再次调用付费视觉模型重跑这 12 段历史视频。对白准确性和跨镜头连续性仍需用户完整播放确认。

## 最终门禁

当前自动化门禁以 GitHub Actions run #202 为最新已完成基线：59 个后端测试、Ruff/Alembic、Vue production build 和 2 条 Playwright E2E 全部通过。中文 RAG 已额外覆盖长连续对白跨块、无主体/省略主语、角色线索、不同对白轮次隔离、普通叙事不误加权和第一人称代词不误标。历史真实 Provider、ComfyUI 与 60 秒成片证据仍有效地证明旧真实链路，但四帧 Inspector、中文对白 RAG v2 的真实 embedding probe、Embedding 批处理和 MUSIC/SFX 混音尚未重新产生一套新的付费 Provider 全链路证据。因此当前结论为“代码与自动化门禁通过；新增真实模型策略和完整用户手动试用仍待最终复验”。Director 的长期自动记忆回写仍明确处于 `DEFERRED_UNTIL_CANONICAL_APPROVAL`，不计入已完成功能。
