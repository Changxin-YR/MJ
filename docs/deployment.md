# 部署

本地：复制 `.env.example` 为 `.env`，更换示例密码与 JWT 密钥，执行 `docker compose up --build -d` 和 `docker compose exec api alembic upgrade head`。访问 `http://localhost:28080`。`PROVIDER_MODE=fake` 用于无付费依赖的开发；要使用真实模型，配置 `DASHSCOPE_API_KEY` 并将模式设为 `dashscope`，然后重建或重启 API、Worker 与 Dispatcher。

生产部署需要在 HTTPS 反向代理后运行，设置 `REFRESH_COOKIE_SECURE=true`，把密钥移入 Secret Manager，迁移作为发布步骤，数据库及对象存储定期备份。MinIO 公网地址由 `MINIO_PUBLIC_ENDPOINT` 提供，用于签名上传；必须限制访问域名和桶策略。现有 Compose 绑定本地端口，适合开发与面试演示，尚不是经生产加固的部署模板。
