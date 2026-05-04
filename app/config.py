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

    openai_api_key: str = Field(default="", validation_alias=AliasChoices("OPENAI_API_KEY", "openai_api_key"))
    pinecone_api_key: str = Field(default="", validation_alias=AliasChoices("PINECONE_API_KEY", "pinecone_api_key"))
    cohere_api_key: str = Field(default="", validation_alias=AliasChoices("COHERE_API_KEY", "cohere_api_key"))
    
    pinecone_index: str = Field(
        default="medical-rag",
        validation_alias=AliasChoices("PINECONE_INDEX", "PINECONE_INDEX_NAME", "pinecone_index"),
    )
    
    embedding_model: str = Field(default="text-embedding-ada-002", validation_alias=AliasChoices("EMBEDDING_MODEL", "embedding_model"))
    chat_model: str = Field(default="gpt-4o-mini", validation_alias=AliasChoices("CHAT_MODEL", "chat_model"))
    reasoning_model: str = Field(default="gpt-4o", validation_alias=AliasChoices("REASONING_MODEL", "reasoning_model"))

    # Feature flags
    enable_live_search: bool = False
    enable_reranking: bool = True
    enable_reasoning_trace: bool = True

    # Scheduler
    scheduler_hour: int = 3  # hour of day (0-23) for daily subscription runs
    scheduler_minute: int = 0

    # Database
    db_path: str = Field(
        default=str(PROJECT_ROOT / "data" / "medical_rag.db"),
        validation_alias=AliasChoices("DB_PATH", "db_path")
    )

    # Authentication
    jwt_secret: str = Field(default="supersecret-medical-rag-key-12345", validation_alias=AliasChoices("JWT_SECRET", "jwt_secret"))
    google_client_id: str | None = Field(default=None, validation_alias=AliasChoices("GOOGLE_CLIENT_ID", "google_client_id"))
    google_client_secret: str | None = Field(default=None, validation_alias=AliasChoices("GOOGLE_CLIENT_SECRET", "google_client_secret"))


settings = Settings()
