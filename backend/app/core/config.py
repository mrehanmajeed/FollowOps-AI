from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "FollowOps AI"
    app_version: str = "0.3.0"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"

    # Single shared operator credential. The MVP has one operator role; a real
    # multi-user deployment replaces this with Supabase Auth JWT verification.
    operator_api_key: SecretStr

    gemini_api_key: SecretStr
    gemini_model: str = "gemini-2.5-flash"

    supabase_url: str
    # Supabase renamed "service role key" to "secret key"; accept either name.
    supabase_secret_key: SecretStr = Field(
        validation_alias=AliasChoices(
            "SUPABASE_SECRET_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
        ),
    )

    zoho_smtp_host: str = "smtp.zoho.com"
    zoho_smtp_port: int = 587
    zoho_smtp_user: str
    zoho_smtp_password: SecretStr
    zoho_use_tls: bool = True

    zoho_imap_enabled: bool = True
    zoho_imap_host: str = "imap.zoho.com"
    zoho_imap_port: int = 993
    zoho_imap_use_ssl: bool = True
    zoho_imap_mailbox: str = "INBOX"
    zoho_imap_poll_interval_seconds: int = 120
    zoho_imap_lookback_days: int = 14
    zoho_imap_max_messages_per_poll: int = 50
    # Default to the SMTP mailbox: one Zoho mailbox sends and receives.
    zoho_imap_user: str = ""
    zoho_imap_password: SecretStr = SecretStr("")

    # "simulated" records the proposed CRM update in the audit log without
    # calling an external CRM. Never report a simulated update as a real one.
    crm_mode: str = "simulated"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _default_imap_credentials(self) -> "Settings":
        if not self.zoho_imap_user:
            self.zoho_imap_user = self.zoho_smtp_user
        if not self.zoho_imap_password.get_secret_value():
            self.zoho_imap_password = self.zoho_smtp_password
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
