import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Telegram
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    telegram_reviewer_user_id: str = Field(default="", alias="TELEGRAM_REVIEWER_USER_ID")
    schedule_time: str = Field(default="08:30", alias="SCHEDULE_TIME")

    # Telegram publication requires a separate operator cutover after human gates.
    editorial_shadow_mode: bool = Field(default=True, alias="EDITORIAL_SHADOW_MODE")
    editorial_cutover_authorized: bool = Field(default=False, alias="EDITORIAL_CUTOVER_AUTHORIZED")
    editorial_quality_gate_report: Path | None = Field(default=None, alias="EDITORIAL_QUALITY_GATE_REPORT")

    # LLM
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.8-flash", alias="GEMINI_MODEL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    llm_request_timeout_seconds: float = Field(default=30, gt=0, alias="LLM_REQUEST_TIMEOUT_SECONDS")
    llm_stage_timeout_seconds: float = Field(default=90, gt=0, alias="LLM_STAGE_TIMEOUT_SECONDS")
    llm_max_attempts: int = Field(default=3, gt=0, le=10, alias="LLM_MAX_ATTEMPTS")
    llm_fallback_models: list[str] = Field(default_factory=list, alias="LLM_FALLBACK_MODELS")

    # Paths
    obsidian_vault_path: Path = Field(
        default_factory=lambda: Path.home() / "Documents" / "Obsidian",
        alias="OBSIDIAN_VAULT_PATH"
    )
    blog_repo_path: Path = Field(
        default_factory=lambda: Path.home() / "blog",
        alias="BLOG_REPO_PATH"
    )
    blog_base_url: str = Field(
        default="https://yourusername.github.io",
        alias="BLOG_BASE_URL"
    )

    # RAG
    qdrant_rag_url: str = Field(default="http://localhost:8765", alias="QDRANT_RAG_URL")



settings = Settings()
