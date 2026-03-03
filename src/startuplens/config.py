"""Application settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All configuration is loaded from environment variables prefixed with SL_."""

    # Database
    database_url: str = ""
    supabase_url: str = ""
    supabase_key: str = ""

    # APIs
    companies_house_api_key: str = ""
    github_token: str = ""
    sec_user_agent: str = "StartupLens research@example.com"
    anthropic_api_key: str = ""

    # CRM (Airtable)
    airtable_api_key: str = ""
    airtable_base_id: str = ""
    airtable_table_name: str = "Deals"

    # Alerting / email
    resend_api_key: str = ""
    alert_email_to: str = ""
    alert_email_from: str = "StartupLens <alerts@startuplens.local>"
    quiet_hours_start: int = 22
    quiet_hours_end: int = 7

    # Governance
    data_retention_days: int = 365
    pii_redaction_enabled: bool = True
    legal_disclaimer_text: str = (
        "For personal research only. Not investment advice or financial promotion."
    )

    model_config = {"env_file": ".env", "env_prefix": "SL_"}


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
