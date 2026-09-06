from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database. `database_url` is what the running app connects with at
    # request time — it MUST be a role without BYPASSRLS, or Row-Level
    # Security enforces nothing (Supabase's own `postgres` role has
    # BYPASSRLS, which is why a second, restricted role is required; see
    # docs/progress.md). `migration_database_url` is the elevated role used
    # only by alembic for DDL; falls back to `database_url` if unset (e.g.
    # a local dev Postgres with one superuser role and no RLS concerns).
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
    migration_database_url: str | None = None

    # Supabase storage
    supabase_url: str = "https://placeholder.supabase.co"
    supabase_service_role_key: str = "placeholder"
    supabase_receipts_bucket: str = "receipts"

    # Gemini
    google_api_key: str = "placeholder"
    gemini_flash_model: str = "gemini-2.5-flash"
    gemini_pro_model: str = "gemini-2.5-pro"
    gemini_embedding_model: str = "gemini-embedding-001"

    # WhatsApp Cloud API
    whatsapp_access_token: str = "placeholder"
    whatsapp_phone_number_id: str = "placeholder"
    whatsapp_app_secret: str = "placeholder"
    whatsapp_verify_token: str = "placeholder"
    whatsapp_alert_template: str = "business_alert"
    whatsapp_otp_template: str = "login_otp"
    whatsapp_template_language: str = "id"

    # Auth
    jwt_secret: str = "dev-secret-do-not-use-in-production"
    jwt_algorithm: str = "HS256"
    owner_token_ttl_minutes: int = 720
    pos_token_ttl_minutes: int = 720

    # App
    environment: str = "development"
    frontend_origin: str = "http://localhost:3000"
    low_stock_days_threshold: float = 3.0
    # Evaluation only (roadmap M9-T6): lets app.eval run the naive text-to-SQL
    # baseline for comparison. Never read by the WhatsApp path.
    eval_text_to_sql: bool = False

    # Backups (roadmap M15-T1). `backup_dir` is where dumps land and it must
    # not be on the database's own host — that is the whole point, and in
    # production it is checked rather than trusted (see app/jobs/backup.py).
    # `backup_database_url` needs a role that can read every tenant's rows:
    # pg_dump runs with row_security off and a restricted role therefore
    # *fails* rather than quietly dumping an empty database, which is why the
    # elevated migration role is the fallback and `database_url` is not.
    backup_dir: str | None = None
    backup_database_url: str | None = None
    backup_keep_daily: int = 30
    backup_keep_monthly: int = 6
    backup_min_bytes: int = 4096          # a dump smaller than this is not a dump
    pg_bin_dir: str | None = None         # where pg_dump/pg_restore live, if not on PATH


@lru_cache
def get_settings() -> Settings:
    return Settings()


_PLACEHOLDER_VALUES = {"placeholder", "CHANGE_ME"}


def is_placeholder(value: str | None) -> bool:
    """True if a credential is still unset.

    Covers both this file's own default sentinel ("placeholder") and
    .env.example's documented sentinel ("CHANGE_ME") — these previously
    didn't match each other, so dry-run mode never actually engaged once a
    real .env existed with unfilled WhatsApp/Supabase credentials still at
    "CHANGE_ME": the code would attempt a real API call and fail with a
    401/etc. instead of degrading gracefully.
    """
    return not value or value in _PLACEHOLDER_VALUES
