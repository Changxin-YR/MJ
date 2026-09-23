# 测试

```powershell
docker compose up -d
docker compose exec api alembic upgrade head
docker compose exec api pytest -q
docker compose exec api alembic check
docker compose exec api ruff check app tests scripts
cd frontend
npm run build
npm run e2e
```

后端测试直接使用 Compose 中的 MySQL、Redis Stack、Qdrant 与 MinIO，覆盖身份、Scope、角色正式版本、镜头状态和乐观锁、RAG 跨项目隔离、Outbox/幂等、Agent 审批与会话撤销、MCP 会话绑定、时间线渲染、签名上传隔离与文件校验。Playwright 一条流程从注册、故事、角色、场景、分镜、生成、审核走到最终视频；另一条验证 Director 生成请求、待审批、批准和继续执行。

真实 Provider 运行脚本：`backend/scripts/provider_smoke.py` 验证模型契约和文件；`integrated_real_smoke.py` 验证 API→Job→Worker→Inspector→MinIO；`final_demo.py` 生成长片；`embedding_smoke.py` 验证真实向量索引与查询；`director_llm_smoke.py` 验证真实 LLM 与 RAG；`inspect_existing_videos.py` 保存逐镜头视觉检查；`rerender_demo.py` 使用已有素材重新渲染。真实模型调用需要 `DASHSCOPE_API_KEY`，不会把密钥写入证据，并会产生费用，不纳入每次 CI。

固定 Agent Eval 数据集覆盖七类规则用例，脚本保存机器可读结果；尚需开放式模型回答质量评估、Callback 重放与 Provider 故障矩阵。并发预算预留、Prompt Injection、跨项目 RAG、MCP 会话绑定、角色权限与上传路径穿越已有针对性测试。验收状态以 [final-evidence.md](final-evidence.md) 中实际运行结果为准。
