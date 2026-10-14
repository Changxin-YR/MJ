# FrameForge AI 接手说明

更新日期：2026-09-24。供新对话或新维护者快速接续；具体测试证据和技术细节以本文链接的仓库文件为准。

## 项目与当前状态

- 项目是 AI 漫剧制作平台：导入故事，维护角色、剧集与分镜，生成图像、视频和配音，经过检查与人工审核后，在五轨时间线渲染成片。
- 仓库位置：`C:\Users\27363\Desktop\MJ`。当前分支 `feat/frameforge-v1`，HEAD `b136332046ad7983784e5d657e67610a50085629`，与 `origin/feat/frameforge-v1` 同步；写本文前工作区干净。
- 截至 2026-09-23 的[验收证据矩阵](final-evidence.md)记录：后端 33 项测试、前端 2 条 E2E、真实模型调用、12 段视频逐镜头检查以及 60 秒成片的技术验收通过。用户完整手动试用仍待完成。后续修改需重新验证受影响的部分。
- 真实成片是 [final.mp4](../evidence/final-demo/final.mp4)，最新渲染参数与资产 ID 见 [rerender-report.json](../evidence/final-demo/rerender-report.json)，当前逐镜头检查见 [inspection.json](../evidence/final-demo/inspection.json)。成片已核对代表帧，完整观感仍需人工播放确认。

## 本机快照与启动

2026-09-24 检查时，Compose 中 `mysql`、`redis`、`qdrant`、`minio`、`api`、`worker`、`dispatcher`、`frontend` 均在运行；`http://127.0.0.1:28081/health/ready` 对四项核心依赖返回 `true`。前端位于 `http://localhost:28080`，API 文档位于 `http://localhost:28080/api/docs`。运行状态会随重启改变，接手时应重新检查。

本机 Git 忽略的 `.env` 当前选择 `PROVIDER_MODE=dashscope`、`EMBEDDING_MODE=dashscope`、`DIRECTOR_LLM_MODE=dashscope`，百炼 Key 已配置但不写入 Git。ComfyUI 本机实例也已恢复，地址为 `http://host.docker.internal:8189`，使用 `D:\Documents\Codex\2026-08-29\zhe\work\ComfyUI-gitee` 的虚拟环境与 `Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors`；切回 `PROVIDER_MODE=comfyui` 时可使用它进行本地图像生成。`.env`、ComfyUI 源码、虚拟环境和模型文件都不在 Git 中；不要在交接消息或提交中粘贴密钥。

在新机器上从仓库根目录启动：

```powershell
Copy-Item .env.example .env
# 按实际环境修改 .env 中的密码、JWT 密钥及 Provider 配置
docker compose up --build -d
docker compose exec api alembic upgrade head
docker compose ps
Invoke-RestMethod http://localhost:28081/health/ready
```

默认 `.env.example` 使用 `PROVIDER_MODE=fake`，可以先完成无付费依赖的流程验证。需要真实图像、视频、配音时，参照 [README](../README.md) 与 [部署说明](deployment.md)配置 `dashscope`；真实模型调用会产生费用。切换 Provider 后重建或重启 `api`、`worker`、`dispatcher`。现有 Compose 用于本地开发与演示，生产部署要求见[部署说明](deployment.md)。

## 核心流程与代码入口

| 领域 | 入口 | 接手要点 |
| --- | --- | --- |
| API 与身份 | `backend/app/main.py`、`backend/app/auth/`、`backend/app/workspace/` | FastAPI 统一挂载 `/api/v1`；资源读取按工作空间和项目 Scope 鉴权。 |
| 业务事实与迁移 | `backend/app/models.py`、`backend/migrations/versions/` | MySQL 保存故事、角色、镜头、Job、审批、资产和审计。迁移使用 Alembic。 |
| 生成链路 | `backend/app/generation/`、`backend/app/workers/`、`backend/app/providers/` | API 同事务写 Job 与 Outbox；Dispatcher 投递 Celery；Worker 调 Provider，校验媒体后保存 MinIO。 |
| Director 与检索 | `backend/app/agent/`、`backend/app/rag/`、`backend/app/mcp/` | LangGraph Director 通过 Tool Gateway 执行受限工具；生成需 PendingAction 审批。MCP stdio 只提供只读工具。 |
| 审核与成片 | `backend/app/agent/inspector.py`、`backend/app/storyboard/`、`backend/app/timeline/` | 真实图像/视频可用 Qwen VL 检查；失败的人工通过必须写理由；时间线用 FFmpeg 渲染。 |
| 前端 | `frontend/src/views/`、`frontend/src/views/project/`、`frontend/e2e/` | Vue 3、Vite、Pinia；项目页覆盖故事、角色、分镜、队列、审核、Director、时间线等。 |

架构图与依赖关系见[架构说明](architecture.md)，权限矩阵见[权限说明](permissions.md)，数据表见[数据模型](data-model.md)，生成状态与预算见[生成说明](generation.md)。

## 验证方法与证据

先启动依赖并执行迁移，再从仓库根目录运行：

```powershell
docker compose run --rm --no-deps -e PROVIDER_MODE=fake api pytest -q
docker compose run --rm --no-deps api ruff check app tests scripts
docker compose run --rm --no-deps api alembic check
npm --prefix frontend run build
npm --prefix frontend run e2e
```

这份交接只核对了仓库、运行容器和 readiness，未重新执行上面的完整测试。已有运行结果及每项的适用范围在[验收证据矩阵](final-evidence.md)；真实 Provider、RAG、Director、ComfyUI 和媒体报告在 `evidence/`，对应脚本在 `backend/scripts/`。运行真实模型脚本前先确认预算和 `.env`。

## 后续工作

1. **完成用户验收**：按[手动验收步骤](testing.md#本机手动验收)走一遍实际产品流程，并完整观看 60 秒成片，记录具体镜头或交互问题。现有 Inspector 的单帧结果不能覆盖对白准确性、字幕全文和跨镜头连续性。
2. **保持本机 ComfyUI 可选出图环境**：Windows 重启后按 [ComfyUI 配置](comfyui.md)重新启动宿主机服务并确认 `http://127.0.0.1:8189/system_stats` 可访问；当前默认 DashScope 模式不依赖它。可选 Compose GPU profile 的镜像构建和容器出图尚无运行验证。
3. **按下一阶段需求确定开发范围**：现有文档记录的待补项包括 RAG 正式版本失效/重建与独立质量评估、Agent 长期记忆审批闭环、Provider 实际账单对账，以及生产部署的密钥管理、备份和对象清理。对应边界见 [RAG](rag.md)、[Agent](agent.md)、[生成说明](generation.md)、[部署说明](deployment.md)。

## 新对话可直接使用的开场说明

> 请接手 `C:\Users\27363\Desktop\MJ` 的 FrameForge AI 项目。先阅读 `docs/HANDOFF.md`、`README.md` 和 `docs/final-evidence.md`，检查当前 Git 与服务状态，再根据我接下来的具体要求继续。仓库现有 60 秒真实成片和验收证据；用户完整手动验收待完成。修改时保留现有证据，按受影响范围验证，并更新交接文档中的状态。
