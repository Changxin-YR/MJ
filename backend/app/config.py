from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://frameforge:change-me@localhost:23306/frameforge"
    redis_url: str = "redis://localhost:26379/0"
    qdrant_url: str = "http://localhost:26333"
    minio_endpoint: str = "localhost:29000"
    minio_public_endpoint: str = "localhost:29000"
    minio_access_key: str = "frameforge"
    minio_secret_key: str = "change-minio-secret"
    minio_bucket: str = "frameforge-assets"
    jwt_secret: str = "local-development-only-change-me"
    refresh_cookie_secure: bool = False
    frontend_origin: str = "http://localhost:28080"
    provider_mode: str = "fake"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    dashscope_image_model: str = "wan2.6-t2i"
    dashscope_image_size: str = "1280*1280"
    dashscope_video_model: str = "wan2.6-i2v-flash"
    dashscope_tts_model: str = "qwen3-tts-flash"
    dashscope_chat_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_vision_model: str = "qwen3-vl-plus"
    embedding_mode: str = "fake"
    dashscope_embedding_model: str = "text-embedding-v4"
    director_llm_mode: str = "fake"
    dashscope_llm_model: str = "qwen-plus"
    comfyui_url: str = "http://comfyui:8188"
    comfyui_checkpoint: str = ""


settings = Settings()
