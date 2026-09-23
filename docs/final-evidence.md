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
| Inspector 与人工复核 | `app/agent/inspector.py`、`app/storyboard/state.py` | `scripts/inspect_existing_videos.py`、`test_generation_flow.py` | [inspection.json](../evidence/final-demo/inspection.json)：当前 12 段真实视频逐镜头复检 12/12 PASS；配音技术检查不覆盖视觉结论；失败检查的人工通过必须记录理由 | PASS（单帧视觉范围） |
| 60 秒真实成片、时间线、字幕 | `app/timeline/`、`scripts/final_demo.py` | `test_timeline_flow.py`、`scripts/rerender_demo.py` | [final.mp4](../evidence/final-demo/final.mp4)、[render report](../evidence/final-demo/rerender-report.json)：60.000 秒、1280×720、H.264/AAC；[中文字幕抽帧](../evidence/final-demo/frame-subtitle.png) | PASS（媒体生成） |
| 资产血缘与审计 | `app/asset/routes.py`、`app/audit/` | Final Demo 脚本 | [lineage.json](../evidence/final-demo/lineage.json)；初次全链路报告记录 146 条审计事件 | PASS |
| 前端构建与核心 E2E | `frontend/src/`、`frontend/e2e/` | `npm run build`、`npm run e2e` | 注册到分镜、生成、审核、时间线、播放成片的浏览器流程；Director→PendingAction→Approve→Resume 流程均通过；[界面截图](../evidence/ui-final.png) | PASS（2 条 E2E） |
| ComfyUI 私有实例与安全适配器 | `app/providers/comfyui.py`、`ops/comfyui/`、`scripts/start-comfyui-local.ps1` | `test_comfyui_adapter.py`、`scripts/comfyui_smoke.py`、`scripts/comfyui_job_smoke.py` | [适配器实图](../evidence/comfyui-live/sample.png)、[实图报告](../evidence/comfyui-live/report.json)、[完整 Job 链路](../evidence/comfyui-live/job-report.json)：RTX 4060 上 1024×576 实图，API→Outbox→Worker→ComfyUI→MinIO 成功；可选 Compose 镜像尚未完成运行验证 | PASS（本机私有实例） |
| Provider Callback 防重放 | `app/generation/callbacks.py`、`provider_callback_receipts` 迁移 | `test_provider_callback.py` | HMAC、时间窗、持久化 nonce、Provider/remote job/状态绑定测试通过；合法回调只写 Outbox 唤醒 Worker，不直接完成任务；原生 DashScope 仍由轮询处理 | PASS（受信任中继协议） |
| Agent Eval 固定数据集与真实回答 | `backend/evals/cases.json`、`backend/tests/test_agent_eval.py`、`scripts/director_quality_eval.py` | `scripts/agent_eval_report.py`、真实 Qwen Plus + RAG 三问 | [规则报告](../evidence/agent-eval/report.json)：7/7；[真实回答](../evidence/agent-eval/open-quality.json)：信号钥匙、截止时间、寄件人姓名三问 3/3，均有项目来源且无生成副作用 | PASS（所列十个样本） |
| 安全与故障矩阵 | `backend/tests/` | 角色横向/纵向权限、RAG 隔离、工具权限、上传/路径穿越、Prompt Injection、Callback Replay、FFmpeg 注入、幂等、预算、Provider 熔断与临时/永久失败、复检失败阻断渲染 | 后端 33 项测试覆盖列出的规则场景；本地 MySQL 迁移与 `alembic check` 通过 | PASS（列出的规则用例） |
| Playwright Flow C | `frontend/src/views/project/DirectorPanel.vue` | `frontend/e2e/director-approval.spec.ts` | Director 提交生成请求，待审批后批准并继续，生成任务出现 | PASS |

## 真实成片说明

完整纵向流程产生 12 张真实图、12 段真实视频、12 段真实配音与一条 60 秒成片。初次报告 [report.json](../evidence/final-demo/report.json) 记录生成和审计阶段。随后依据视觉检查修正四个镜头，并为钟楼造型连续性再修正一个镜头；最新成片资产 ID 与媒体参数以 [rerender-report.json](../evidence/final-demo/rerender-report.json) 为准。

初次报告的 `inspection` 列表是当时配音任务的技术检查，不代表视觉检查。该覆盖错误现已修正。当前视频资产的 Qwen VL 结果以 [inspection.json](../evidence/final-demo/inspection.json) 为准，12/12 PASS。静帧无法证明对白准确、字幕全文或跨镜头连续性；已另行检查成片的代表帧，最终观感仍需用户完整播放确认。

## 最终门禁

列出的自动化、真实 Provider、12 段视频复检及成片技术验收已通过。用户的完整手动试用尚未进行，因此此处记录为“技术验收通过，用户验收待完成”。ComfyUI 本机私有实例及完整 Job 链路已验证；可选 Compose profile 的 GPU 镜像构建与出图仍未验证，不作为本机私有实例门禁的替代证据。
