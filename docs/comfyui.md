# ComfyUI 私有图像实例

FrameForge 的 IMAGE Job 经 Provider Gateway 调用固定的 `basic-t2i-v1` 工作流。Vue 不连接 ComfyUI。适配器只接受 `http://comfyui:8188` 或本机 Docker 桥接地址 `http://host.docker.internal:8189`，并限制 checkpoint 文件名、节点、输出路径和文件大小。

## 已验证的本机实例

2026-09-23 在 RTX 4060 Laptop GPU（8 GB）上，使用 ComfyUI `0.34.0`、`Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors`、`--lowvram`，服务仅监听 `127.0.0.1:8189`。适配器真实出图为 1024×576 PNG，见 [样图](../evidence/comfyui-live/sample.png) 与 [适配器报告](../evidence/comfyui-live/report.json)。通过 HTTP API 创建的 IMAGE Job 由 Outbox/Worker 执行，最终资产进入 MinIO，见 [完整链路报告](../evidence/comfyui-live/job-report.json)。本机 `.env` 已设置 `PROVIDER_MODE=comfyui`、`COMFYUI_URL=http://host.docker.internal:8189` 和上述 checkpoint。该模式下 VIDEO、VOICE 使用 FakeProvider。

重启 Windows 后，在已有 ComfyUI 目录执行：

```powershell
.\scripts\start-comfyui-local.ps1 -ComfyUIPath 'D:\path\to\ComfyUI'
docker compose up -d api worker dispatcher
```

本机实例用已有的 ComfyUI 虚拟环境与模型。源代码与模型不在 Git 仓库中；运行本机模式需要可用的 `.venv`、CUDA PyTorch 和对应 checkpoint。模型名必须与 ComfyUI `CheckpointLoaderSimple` 列出的名字完全一致。

## 可复现的 Compose 实例

`comfyui` 是可选 Compose profile，只有内部服务端口，不映射宿主机端口。脚本通过 SSH 获取固定的 ComfyUI 源码提交，并可使用已有 checkpoint 目录：

```powershell
.\scripts\setup-comfyui.ps1 -ModelDirectory 'D:\path\to\ComfyUI\models\checkpoints' -Checkpoint 'your-model.safetensors'
```

若没有模型，脚本可通过 Hugging Face `hf` CLI 下载 Comfy Org 的 SD 1.5 FP16 checkpoint。随后把 `.env` 设为：

```text
PROVIDER_MODE=comfyui
COMFYUI_URL=http://comfyui:8188
COMFYUI_CHECKPOINT=v1-5-pruned-emaonly-fp16.safetensors
COMFYUI_MODELS_PATH=./.local/comfyui-models/checkpoints
```

使用已有模型目录时，`COMFYUI_MODELS_PATH` 填实际目录，`COMFYUI_CHECKPOINT` 填目录内实际模型名，然后执行 `docker compose --profile comfyui up -d --build comfyui api worker dispatcher`。需要 NVIDIA 驱动、Docker GPU 支持与足够磁盘空间。本机已验证 `docker run --gpus all` 能识别 RTX 4060；Compose 镜像构建与容器出图另行记录，不能用本机桥接的验证结果代替。

ComfyUI 的[官方安装说明](https://github.com/Comfy-Org/ComfyUI#manual-install-windows-linux)说明 checkpoint 目录；[Comfy Org 模型归档](https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive)提供上述 FP16 checkpoint 并标注 `creativeml-openrail-m` 许可证。使用模型前应核对其许可证与业务用途。
