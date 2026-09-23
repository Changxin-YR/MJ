# FrameForge V1.3 验收证据矩阵

更新于 2026-09-23。状态只反映已执行的测试与运行证据；`PASS` 表示该行列出的范围已验证，不表示整个冻结设计已完成。

| 要求 | 实现 | 测试或验证 | 运行证据 | 状态 |
| --- | --- | --- | --- | --- |
| MySQL 事实源、迁移、Compose | `backend/app/models.py`、`backend/alembic/`、`docker-compose.yml` | `alembic upgrade head`、`alembic check` | MySQL/Redis/Qdrant/MinIO/API/Worker/Dispatcher/Frontend 已启动 | PASS |
| 身份、角色、项目 Scope | `app/auth/`、`app/workspace/` | `test_core_flow.py`、`test_security_roles.py` | Viewer 生成 403，Editor 编辑 LOCKED 镜头 409，外部用户读取项目 404 | PASS |
| 乐观锁、镜头状态、角色版本 | `app/storyboard/`、`app/character/` | `test_core_flow.py`、`test_state.py` | 版本冲突与状态转换测试通过 | PASS |
| RAG 项目隔离 | `app/rag/service.py` | `test_rag_scope.py` | Project A 查询 Project B 独有内容返回零个 B 分块 | PASS |
| 真实向量与语义检索 | `app/providers/embeddings.py`、`app/rag/service.py` | `scripts/embedding_smoke.py` | [embedding.json](../evidence/final-demo/embedding.json)：`text-embedding-v4`，1024 维，真实查询返回本项目来源 | PASS（单项目质量样本） |
| Director、Tool Gateway、审批与撤销 | `app/agent/` | `test_director.py`、`test_prompt_injection.py` | PendingAction 审批后执行；会话撤销后 resume 被拒；恶意故事内容无法触发危险工具 | PASS |
| 真实 Director 文本分析 | `app/providers/llm.py`、`app/agent/workflow.py` | `scripts/director_llm_smoke.py` | [director-llm.json](../evidence/final-demo/director-llm.json)：Qwen Plus、真实 RAG、只读工具、审计 Trace | PASS（只读 ANALYZE） |
| MCP Adapter | `app/mcp/server.py` | `test_mcp_adapter.py`、`scripts/mcp_smoke.py` | [mcp.json](../evidence/final-demo/mcp.json)：实际 stdio 协议发现 3 个只读工具并读取本项目上下文；跨会话调用测试拒绝 | PASS（本地只读适配器） |
| Job、Outbox、幂等、预算 | `app/generation/`、`app/workers/` | `test_generation_flow.py`、`test_budget.py` | Redis 暂时故障时 MySQL Job 留存；同一幂等键返回同一 Job；预算拒绝超限请求 | PASS（现有测试范围） |
| 真实图像、视频、配音 Provider | `app/providers/dashscope.py` | `scripts/provider_smoke.py`、`scripts/integrated_real_smoke.py` | [provider report](../evidence/real-provider/report.json)、[integrated report](../evidence/integrated-real/report.json)：Wan T2I、Wan I2V、Qwen TTS | PASS |
| 文件上传与隔离 | `app/asset/` | `test_upload.py` | 隔离前缀签名上传、Finalize 验证后 READY；非法素材被拒 | PASS |
| Inspector 与人工复核 | `app/agent/inspector.py`、`app/storyboard/state.py` | `scripts/inspect_existing_videos.py` | [inspection.json](../evidence/final-demo/inspection.json)：12 段真实视频逐镜头复检，8 PASS、4 FAIL，单帧无法证明的项目保留 `UNVERIFIED` | PARTIALLY IMPLEMENTED |
| 60 秒真实成片、时间线、字幕 | `app/timeline/`、`scripts/final_demo.py` | `test_timeline_flow.py`、`scripts/rerender_demo.py` | [final.mp4](../evidence/final-demo/final.mp4)、[render report](../evidence/final-demo/rerender-report.json)：60.000 秒、1280×720、H.264/AAC；[中文字幕抽帧](../evidence/final-demo/frame-subtitle.png) | PASS（媒体生成） |
| 资产血缘与审计 | `app/asset/routes.py`、`app/audit/` | Final Demo 脚本 | [lineage.json](../evidence/final-demo/lineage.json)；初次全链路报告记录 146 条审计事件 | PASS |
| 前端构建与核心 E2E | `frontend/src/`、`frontend/e2e/` | `npm run build`、`npm run e2e` | 注册到分镜、生成、审核、时间线、播放成片的浏览器流程；Director→PendingAction→Approve→Resume 流程均通过；[界面截图](../evidence/ui-final.png) | PASS（2 条 E2E） |
| ComfyUI 安全适配器 | `app/providers/comfyui.py` | `test_comfyui_adapter.py` | 固定节点、内网地址、受控参数与输出路径契约测试通过；无真实实例运行证据 | PARTIALLY IMPLEMENTED |
| Provider Callback 防重放 | — | — | 当前使用主动轮询，不接收云回调 | NOT IMPLEMENTED |
| Agent Eval 固定数据集 | `backend/evals/cases.json`、`backend/tests/test_agent_eval.py` | `scripts/agent_eval_report.py` | [agent eval report](../evidence/agent-eval/report.json)：七类固定规则用例 7/7 PASS；不涵盖开放式模型回答质量 | PASS（规则用例） |
| 完整安全与故障矩阵 | `backend/tests/` 部分覆盖 | 角色、Scope、上传、路径穿越、注入、幂等、并发预算测试 | Callback 重放、Provider 故障及独立 FFmpeg 注入用例未全覆盖 | PARTIALLY IMPLEMENTED |
| Playwright Flow C | `frontend/src/views/project/DirectorPanel.vue` | `frontend/e2e/director-approval.spec.ts` | Director 提交生成请求，待审批后批准并继续，生成任务出现 | PASS |

## 真实成片说明

完整纵向流程产生 12 张真实图、12 段真实视频、12 段真实配音与一条 60 秒成片。初次报告 [report.json](../evidence/final-demo/report.json) 记录生成和审计阶段；成片随后使用已生成素材补上 CJK 字体重新渲染，最新资产 ID 与媒体参数以 [rerender-report.json](../evidence/final-demo/rerender-report.json) 为准。

初次报告的 `inspection` 列表是每个镜头最后一次配音任务的技术检查，不代表视觉检查。逐视频的 Qwen VL 结果以 [inspection.json](../evidence/final-demo/inspection.json) 为准。四项 FAIL 涉及城市灯光连续性、角色数量或道具一致性，需要人工逐镜头复核和必要的重生成；不能把它们计为视觉验收通过。

## 最终门禁

当前不能标记 `FRAMEFORGE V1 COMPLETE`。ComfyUI Adapter 尚无真实实例验证，Callback 安全与完整安全测试矩阵尚未达到验收条件，视觉复检也留下四个待处理镜头。固定 Agent Eval 的规则用例已通过，但开放式回答质量尚未评价。已通过的真实 Provider 和 60 秒成片证明主流程可运行，但不替代上述门禁。
