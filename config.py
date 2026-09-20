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
    schedule_time: str = Field(default="08:30", alias="SCHEDULE_TIME")

    # LLM
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.8-flash", alias="GEMINI_MODEL")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

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
