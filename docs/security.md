# 安全边界

- Access JWT 绑定服务端 Session；Refresh Token 仅存 HttpOnly Cookie，轮换时记录已用哈希，重放会撤销会话。
- 项目 API 均重新鉴权；资源查询从 SQL 条件约束工作空间和项目；Agent resume 与 Tool Gateway 实时复查。
- 工具白名单和严格参数集合禁止 Agent 直接访问 SQL、Redis、对象存储或任意外部 URL。
- 上传采用短期签名 PUT 到隔离前缀。Finalize 验证上传大小、声明 MIME、文件签名、解码/媒体元数据及哈希，再提升为 READY；未就绪资产不可下载。用户提供的文件名不用于对象键。
- Provider 结果下载只允许阿里云结果域名，并限制体积与重定向；FFmpeg 使用固定程序与参数数组。审计保留安全摘要，不写 API Key。

MCP stdio 适配器仅提供只读工具，并将访问令牌绑定到 Director Run 的用户与会话；每次调用继续经过 Tool Gateway。

ComfyUI 适配器固定工作流节点和参数，限制私有服务地址、检查点文件名及返回图像路径；尚未与真实 ComfyUI 实例联调。当前实际运行使用主动轮询，不开放云 Provider Callback，因此没有回调签名、时间窗与重放验证。部署前还需 TLS、密钥管理、非 root Worker、备份与对象过期清理。
