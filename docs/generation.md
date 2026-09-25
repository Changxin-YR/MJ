# 生成与审核

API 在事务中锁定 Project 预算行，预留估算成本，写入 GenerationJob 与 OutboxEvent。Dispatcher 可重复投递，Worker 依靠 Job 状态、租约和幂等键避免重复完成；失败按指数退避重试。异步视频先持久化远端任务 ID，再由 Outbox 周期轮询，不假定提交即得到文件。

Provider Registry 按启用状态、能力、优先级、健康与熔断状态确定路由。`PROVIDER_MODE=fake` 是默认测试模式；`dashscope` 调用 `wan2.6-t2i`、`wan2.6-i2v-flash`、`qwen3-tts-flash`。`comfyui` 图像适配器只向私有 ComfyUI 地址提交固定的 `basic-t2i-v1` 工作流，并要求管理员配置检查点文件名；禁止用户传任意工作流 JSON。适配器已有 RTX 4060 上的真实出图与 Job→Worker→MinIO 证据，见 [ComfyUI 配置](comfyui.md)。所有模型输出先验证大小、Magic Number、解码或 `ffprobe`，再入 MinIO。真实图像/视频交 Qwen VL 做结构化检查；视频按多个时间点抽帧，而不是只看首帧。图像与视频 Prompt 均附加“可见文字只能为简体中文”的硬约束，DashScope 图像/视频关闭自动 Prompt 改写；Inspector 一旦判定可见文字语言失败，该结果不可人工 override，必须重新生成。其他视觉失败仍可在记录理由后人工复核。素材内容变化会使旧媒体、旧审核或旧成片引用失效。

视频预算按当前配置的 720P 静音每秒单价估算；模型媒体接口未返回实际账单金额，成功任务以预留额结算，后续需要账单对账。项目 UI 中的成本数值应理解为估算结算值。配置价格应在部署时根据供应商价目更新。

五轨时间线由 VIDEO、VOICE、MUSIC、SFX、SUBTITLE 组成。VIDEO/VOICE/SUBTITLE 由审核后的镜头同步生成；MUSIC/SFX 支持上传经校验的 WAV，并设置开始时间，镜头重新同步不会删除这些手工音频项。最终渲染通过 FFmpeg 拼接视频、烧录字幕并混合人声/音乐/音效；当前 MUSIC 与 SFX 使用保守固定混音增益，不提供专业 DAW 级包络或自动 ducking。输出 MP4 入 MinIO，当前最终资产可回溯到镜头、任务、Provider、Agent Run 与故事来源。
