# Provider Relay Callback

`POST /api/v1/provider-callbacks/dashscope` 接收受信任中继的通知。当前 DashScope 视频任务仍可仅通过 Worker 主动轮询完成；此接口用于已配置共享密钥的回调中继，不能直接当作 DashScope 原生 webhook 使用。

服务器要求 `PROVIDER_CALLBACK_SECRET` 至少 32 字符，并验证以下请求头：

```text
X-FrameForge-Timestamp: Unix 秒时间戳
X-FrameForge-Nonce: 16–128 位 A-Z、a-z、0-9、_、-
X-FrameForge-Signature: 64 位小写十六进制 HMAC-SHA256
```

签名消息是原始字节串 `provider + "\n" + timestamp + "\n" + nonce + "\n" + raw_json_body`，其中 `provider` 为路径中的 `dashscope`，密钥为 `PROVIDER_CALLBACK_SECRET`。默认时间窗为前后 300 秒。JSON 正文：

```json
{"provider_job_id":"FrameForge GenerationJob UUID","remote_job_id":"Provider remote task ID","status":"SUCCEEDED"}
```

`status` 也可为 `FAILED`。验证还要求任务的 Provider、VIDEO 类型、remote ID 与 `RUNNING` 状态匹配。MySQL 中 `(provider, nonce)` 唯一约束保存已接收 nonce，防止重放并覆盖并发请求。通过验证后，同一事务写入回调收据、审计与 Outbox 事件。Worker 收到事件后仍向 Provider 查询状态并拉取媒体；回调正文不能直接让 Job 成功。

测试 `test_provider_callback.py` 覆盖有效通知、重复 nonce、正文篡改、过期时间、错误 remote/job 绑定、错误任务状态和未配置密钥。签名与 nonce 不写入审计摘要。
