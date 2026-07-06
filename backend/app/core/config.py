from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
