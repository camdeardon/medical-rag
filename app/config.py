from pathlib import Path

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    pinecone_api_key: str = ""
    cohere_api_key: str = ""
    pinecone_index: str = Field(
        default="medical-rag",
        validation_alias=AliasChoices("PINECONE_INDEX", "PINECONE_INDEX_NAME"),
    )
    embedding_model: str = "text-embedding-ada-002"
    chat_model: str = "gpt-4o-mini"
    reasoning_model: str = "gpt-4o"  # stronger model for CoT reasoning

    # Feature flags
    enable_live_search: bool = True
    enable_reranking: bool = True
    enable_reasoning_trace: bool = True

    # Scheduler
    scheduler_hour: int = 3  # hour of day (0-23) for daily subscription runs
    scheduler_minute: int = 0

    # Database
    db_path: str = str(PROJECT_ROOT / "data" / "medical_rag.db")


settings = Settings()
