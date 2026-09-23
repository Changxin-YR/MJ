# FrameForge AI

AI 漫剧制作平台。实现与验收状态见 [架构](docs/architecture.md) 和 [证据矩阵](docs/final-evidence.md)。可播放的真实 60 秒成片在 [evidence/final-demo/final.mp4](evidence/final-demo/final.mp4)。

## Local development

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose exec api alembic upgrade head
```

Open `http://localhost:28080`. API documentation is at `http://localhost:28080/api/docs`.

首次账号可在注册页创建。默认使用 FakeProvider；真实多模态能力需要配置 `.env` 中的 `DASHSCOPE_API_KEY` 并将 `PROVIDER_MODE` 改为 `dashscope`。真实语义检索设置 `EMBEDDING_MODE=dashscope`，真实 Director 分析设置 `DIRECTOR_LLM_MODE=dashscope`。变更向量模型后，对故事重新执行索引。

本地 ComfyUI 出图与可选私有 Compose 实例见 [ComfyUI 配置](docs/comfyui.md)。Provider 回调中继的签名协议见 [Callback 协议](docs/provider-callback.md)。

## Verification

```powershell
docker compose exec api pytest -q
docker compose exec api ruff check app tests scripts
docker compose exec api alembic check
npm --prefix frontend run build
npm --prefix frontend run e2e
```

MCP stdio 适配器以 `python -m app.mcp.server` 启动，需将短期访问令牌放在进程环境变量 `FRAMEFORGE_MCP_ACCESS_TOKEN` 中。仅提供项目上下文、知识检索和任务状态三种只读工具；每次调用绑定 Director Run 的用户会话并经过 Tool Gateway。

证据矩阵记录真实运行结果及未完成项，不以 FakeProvider 测试替代真实模型验收。
