# 生成与审核

API 在事务中锁定 Project 预算行，预留估算成本，写入 GenerationJob 与 OutboxEvent。Dispatcher 可重复投递，Worker 依靠 Job 状态、租约和幂等键避免重复完成；失败按指数退避重试。异步视频先持久化远端任务 ID，再由 Outbox 周期轮询，不假定提交即得到文件。

Provider Registry 按启用状态、能力、优先级、健康与熔断状态确定路由。`PROVIDER_MODE=fake` 是默认测试模式；`dashscope` 调用 `wan2.6-t2i`、`wan2.6-i2v-flash`、`qwen3-tts-flash`。`comfyui` 图像适配器只向私有 ComfyUI 地址提交固定的 `basic-t2i-v1` 工作流，并要求管理员配置检查点文件名；禁止用户传任意工作流 JSON。适配器已有 RTX 4060 上的真实出图与 Job→Worker→MinIO 证据，见 [ComfyUI 配置](comfyui.md)。所有模型输出先验证大小、Magic Number、解码或 `ffprobe`，再入 MinIO。真实图像/视频交 Qwen VL 做结构化检查；失败结论必须附人工复核理由才能批准，复检失败或素材改变会阻止时间线同步和渲染。

视频预算按当前配置的 720P 静音每秒单价估算；模型媒体接口未返回实际账单金额，成功任务以预留额结算，后续需要账单对账。项目 UI 中的成本数值应理解为估算结算值。配置价格应在部署时根据供应商价目更新。

五轨时间线按镜头顺序拼接视频、配音和字幕；FFmpeg 参数使用数组传入，输出 MP4 入 MinIO，最终资产可回溯到镜头、任务、Provider、Agent Run 与故事来源。
