from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str
    postgres_port: int = 5432

    celery_broker_url: str
    celery_result_backend: str

    app_env: str = "development"
    log_level: str = "info"
    rate_limit_enabled: bool = True

    beat_etl_hour: int = 0
    beat_etl_minute: int = 0

    cors_origins: str = "http://localhost:5173"

    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    lastfm_api_key: str = ""

    # LLM / Agent
    anthropic_api_key: str = ""
    llm_provider: str = "anthropic"
    agent_model: str = "claude-haiku-4-5"
    agent_judge_model: str = "claude-sonnet-4-6"
    agent_max_iterations: int = 6

    # Agent SQL
    agent_sql_timeout_ms: int = 5000
    agent_sql_row_cap: int = 200

    # Agent memory
    agent_memory_ttl_seconds: int = 86400
    agent_max_messages: int = 20
    agent_local_tz: str = "America/New_York"

    # Agent read-only role
    agent_readonly_db_user: str = "agent_readonly"
    agent_readonly_db_password: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def async_database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
