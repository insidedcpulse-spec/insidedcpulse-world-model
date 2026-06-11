from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Postgres
    database_url: str = "postgresql://insidedcpulse:insidedcpulse@postgres:5432/insidedcpulse"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Internal protected endpoints (e.g. /world/commit)
    internal_api_key: str = "change-me-internal-key"

    # Bootstrap admin key, used to register new agents via /api/v1/agents/register
    admin_api_key: str = "change-me-admin-key"

    # Validation thresholds (deterministic rules)
    accept_score_threshold: float = 0.5
    min_reputation_to_submit: float = 0.05
    max_payload_bytes: int = 8192

    # Rate limiting (requests per window, per agent)
    rate_limit_window_seconds: int = 60
    rate_limit_vision_per_window: int = 30
    rate_limit_read_per_window: int = 120

    # Self-serve registration rate limit (per IP)
    rate_limit_register_per_window: int = 5
    rate_limit_register_window_seconds: int = 86400

    # Worker
    queue_key: str = "world:queue:pending"
    stream_channel: str = "world:stream"
    worker_poll_timeout_seconds: int = 5

    # Reputation adjustment per outcome
    reputation_step_accept: float = 0.02
    reputation_step_reject: float = 0.05


settings = Settings()
