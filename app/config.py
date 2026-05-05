from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    github_webhook_secret: str = ""
    github_token: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    bot_username: str = ""

    # Where ChromaDB persists per-repo indexes.
    index_dir: Path = Path("./indexes")

    # Per-section character budgets fed to the LLM prompt.
    diff_budget: int = 30_000
    rag_budget: int = 18_000

    # Top-k similar chunks pulled from the vector store per changed file.
    rag_top_k: int = 8

    # Chunking parameters for the indexer.
    chunk_size: int = 1000
    chunk_overlap: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()
