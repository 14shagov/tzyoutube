"""Application configuration from environment variables."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration comes from the environment (12-factor)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: str = "dev"  # dev | production
    secret_key: str = "dev-insecure-secret-key-change-me"

    # Database
    database_url: str = "postgresql+asyncpg://tzyoutube:tzyoutube@localhost:5432/tzyoutube"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # S3 / MinIO
    s3_endpoint: str = "localhost:9000"
    s3_public_endpoint: str = "localhost:9000"
    s3_access_key: str = "devkey"
    s3_secret_key: str = "devsecret"
    s3_secure: bool = False
    # Explicit region for BOTH Minio clients: without it minio-py asks the
    # server GetBucketLocation (?location=) over the network on every
    # presigned_* call — unreachable from the web container via the public
    # host. Must match the server's MINIO_REGION (docker-compose).
    s3_region: str = "us-east-1"
    # Task 7: presigned GET TTL. Public videos get 1h; PRIVATE videos only
    # 15 min — a leaked link must not live for 4 hours.
    presigned_ttl_seconds: int = 3600
    presigned_ttl_private_seconds: int = 900

    # Uploads
    max_upload_size: int = 512 * 1024 * 1024

    # Sessions
    session_ttl_days: int = 14

    # CSRF: origins allowed for state-changing browser requests (comma
    # separated). Behind nginx the app container never sees the public
    # host in scope["server"] (that is the container socket), so this
    # list plus the Host header fallback is the source of truth (m01882).
    allowed_origins: str = "http://localhost,http://127.0.0.1"

    # Cookies: Secure flag is a DEPLOYMENT decision (HTTPS only), not an
    # env-mode decision — a production deploy over plain HTTP must still
    # keep working (m01882 task 5).
    cookie_secure: bool = False

    @model_validator(mode="after")
    def _reject_insecure_production(self) -> "Settings":
        if self.is_production and self.secret_key.startswith("dev-insecure"):
            raise ValueError(
                "SECRET_KEY must be set to a real secret in production "
                "(got insecure dev default)"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
