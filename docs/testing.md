# 测试

```powershell
docker compose up -d --build api worker dispatcher frontend
docker compose exec api alembic upgrade head
docker compose run --rm --no-deps -e PROVIDER_MODE=fake api pytest -q
docker compose run --rm --no-deps api alembic check
docker compose run --rm --no-deps api ruff check app tests scripts
cd frontend
npm run build
npm run e2e
```

后端测试直接使用 Compose 中的 MySQL、Redis Stack、Qdrant 与 MinIO，覆盖身份、Scope、角色正式版本、镜头状态和乐观锁、中文语义/连续对白切块、RAG 跨项目隔离、重复索引与失败重建保护、Embedding 批处理契约、Outbox/幂等、Agent 审批与会话撤销、MCP 会话绑定、五轨时间线渲染、MUSIC/SFX 混音、签名与直传素材校验。Playwright 一条流程从注册、故事、角色、场景、分镜、生成、审核走到最终视频；另一条验证 Director 生成请求、待审批、批准和继续执行。

真实 Provider 运行脚本：`backend/scripts/provider_smoke.py` 验证模型契约和文件；`integrated_real_smoke.py` 验证 API→Job→Worker→Inspector→MinIO；`final_demo.py` 生成长片；`embedding_smoke.py` 验证真实向量索引与查询；`director_llm_smoke.py` 验证真实 LLM 与 RAG；`inspect_existing_videos.py` 保存逐镜头视觉检查；`rerender_demo.py` 使用已有素材重新渲染。真实模型调用需要 `DASHSCOPE_API_KEY`，不会把密钥写入证据，并会产生费用，不纳入每次 CI。

固定 Agent Eval 数据集覆盖七类规则用例，脚本保存机器可读结果；真实 Qwen Plus + RAG 对故事事实另做三问回答评估。Callback 重放、Provider 故障矩阵、并发预算预留、Prompt Injection、跨项目 RAG、MCP 会话绑定、角色权限、上传路径穿越及复检失败阻断渲染已有针对性测试。验收状态以 [final-evidence.md](final-evidence.md) 中实际运行结果为准。

## 本机手动验收

1. 在仓库根目录执行 `docker compose ps`，确认 `api`、`worker`、`dispatcher`、`frontend`、MySQL、Redis、Qdrant、MinIO 正在运行；打开 `http://localhost:28081/health/ready` 查看依赖状态。
2. 打开 `http://localhost:28080`，注册或登录，创建工作空间和项目，再依次创建故事、角色、分镜。检查生成队列、镜头审核、时间线同步和成片播放。已有 60 秒真实成片见 `evidence/final-demo/final.mp4`。
3. 当前本机 `.env` 的 `PROVIDER_MODE=comfyui` 会由私有 ComfyUI 真实生成图像，视频和配音使用 FakeProvider。要手动体验三类真实模型，将 `.env` 中 `PROVIDER_MODE` 改为 `dashscope`，确认 `DASHSCOPE_API_KEY` 已配置，然后执行 `docker compose up -d --force-recreate api worker dispatcher`。测试后可改回 `comfyui` 并再次执行该命令。
4. Windows 重启后，Docker Desktop 的 Compose 服务与宿主机 ComfyUI 是两套进程。Compose 服务用 `docker compose up -d` 启动；宿主机 ComfyUI 用 `scripts/start-comfyui-local.ps1` 启动，具体路径见 [ComfyUI 配置](comfyui.md)。
